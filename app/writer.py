"""Написання SEO-тексту за ТЗ: автор → автоматична перевірка → редактор (до N раундів)."""
import re
from pathlib import Path

import httpx

from . import config, llm
from . import norms

REFERENCE = (Path(__file__).parent / "examples" / "text_serm_ua.md").read_text(encoding="utf-8")
LANG_NAME = {"ua": "українською", "ru": "російською"}
LT_LANG = {"ua": "uk-UA", "ru": "ru-RU"}

WRITER_SYS = """Ти — досвідчений SEO-копірайтер і редактор digital-агенції. Пишеш продаючі, експертні SEO-тексти для сторінок послуг суворо за технічним завданням (ТЗ).

ПРАВИЛА
1. Структура — точно за ТЗ: усі H1/H2/H3 дослівно, у тому самому порядку, нічого не додавай і не прибирай. «CTA» і «FAQ» — це службові розділи (## CTA, ## FAQ).
2. Обсяг кожного блоку — за коментарем ТЗ (±15%). Загальний обсяг — строго в межах, указаних у ТЗ (знаки з пробілами, без розмітки).
3. Виконай усе, що просить коментар: таблиці, нумеровані етапи, списки, формули з прикладом, порівняння.
4. Ключі — у межах рекомендованої кількості входжень на весь текст (разом із заголовками). Головний ключ — у першому абзаці. Не став два ключі в одне речення. LSI — у межах діапазону, не перевищуй верхню межу. Чергуй синоніми, без переспаму.
   Пріоритети, якщо вимоги конфліктують: структура → загальний обсяг → ключі → LSI (нижні межі LSI — бажані, верхні — обов’язкові).
5. Мова: пиши {lang_name}, природно, як носій мови; без кальок, русизмів/суржику (для UA), без канцеляризмів і «води». Короткі абзаци (до 3–4 речень). Тон — експертний, від імені компанії («ми»), звернення до читача на «ви»/«вы».
6. ГРАМОТНІСТЬ: жодних орфографічних, пунктуаційних, граматичних, стилістичних і смислових помилок. Узгодження слів, правильні відмінки, апострофи (UA), лапки «ялинки», тире — між частинами речення.
7. ФАКТИ: використовуй лише факти про компанію з блоку «Про компанію». НЕ вигадуй ціни, кейси, цифри клієнта, імена, сертифікати, партнерства, гарантії, нові зобов’язання. Де ТЗ просить дані «УТОЧНИТИ» — став плейсхолдер у квадратних дужках: «[уточнити: вартість пакетів]». Ціни ринку й назви конкурентів у текст не переносити. Загальновідомі факти можна, умовні приклади розрахунків позначай як умовні.
8. CTA: текст за коментарем, потім окремим рядком кнопка: **[Текст кнопки]**.
9. FAQ: кожне питання — ### H3, відповідь 250–400 знаків, по суті, без повтору тексту вище.
10. Без емодзі. Таблиці — у markdown.

ФОРМАТ ВІДПОВІДІ: лише markdown-текст сторінки, без пояснень. # — H1, ## — H2 (а також «## CTA», «## FAQ»), ### — H3 і питання FAQ.

ЕТАЛОН РІВНЯ (інша сторінка, інша компанія — НЕ копіюй факти, бери лише рівень якості, тон, щільність і форматування):
""" + REFERENCE

EDITOR_SYS = """Ти — шеф-редактор і коректор. Тобі дано ТЗ, текст і звіт автоматичної перевірки. Поверни ВИПРАВЛЕНИЙ ПОВНИЙ текст сторінки в markdown (без пояснень), який:
- виправляє ВСІ проблеми зі звіту (обсяг, заголовки, кількість ключів/LSI, довжина відповідей FAQ, орфографія);
- не має орфографічних, граматичних, пунктуаційних, стилістичних і смислових помилок; мова — природна {lang_name};
- зберігає структуру і дослівні заголовки з ТЗ, факти й плейсхолдери [уточнити], не додає вигаданих фактів;
- не погіршує текст: якщо пункт звіту — хибна тривога (наприклад, LanguageTool помиляється щодо терміна), залиш як є.
Якщо треба скоротити — прибирай «воду», а не зміст. Якщо треба додати — додавай корисну конкретику, а не повтори.
Пріоритети: структура → загальний обсяг і обсяги розділів → ключі → верхні межі LSI → нижні межі LSI."""


# ---------------------------------------------------------------- ТЗ → текст для промпту
def tz_brief(tz: dict, lang: str, project: dict, research: dict | None = None) -> str:
    L = 0 if lang == "ru" else 1
    out = [f"## ТЗ ({lang.upper()}-версія): {tz.get('name_' + lang) or tz.get('name_ua')}",
           f"Загальний обсяг: {tz.get('volume')}",
           f"Шаблон: {tz.get('template')}"]
    if project.get("facts"):
        out.append(f"\n## Про компанію (лише ці факти можна використовувати)\n{project['facts']}")
    if project.get("domain"):
        out.append(f"Сайт: {project['domain']}")
    if project.get("notes"):
        out.append(f"Додаткові вимоги: {project['notes']}")
    out.append("\n## Структура з коментарями")
    for b in tz["blocks"]:
        t = b[0]
        if t in ("h1", "h2", "h3"):
            out.append(f"{t.upper()}: {b[1 + L]}")
        elif t == "c":
            out.append(f"   [коментар] {b[1]}")
        elif t == "cta":
            out.append("CTA")
        elif t == "faq":
            out.append("FAQ")
        elif t == "q":
            out.append(f"   питання FAQ: {b[1 + L]}")
    secs = [f"- «{t}»: {lo}–{hi} зн." for t, lo, hi in section_targets(tz, lang) if lo]
    if secs:
        out.append("\n## Цільовий обсяг розділів (разом з підрозділами, знаки з пробілами)")
        out += secs
    eff = norms.effective(tz, lang)
    out.append("\n## Ключові запити (входжень на весь текст, разом із заголовками)")
    out += [f"- {k}: {lo}–{hi}" + (f" ({note})" if note else "") for kind, k, lo, hi, f, h, note in eff if kind == "ключ"]
    out.append("\n## LSI (діапазон входжень, разом із заголовками)")
    out += [f"- {k}: {lo}–{hi}" + (f" ({note})" if note else "") for kind, k, lo, hi, f, h, note in eff if kind == "LSI"]
    paa = []
    for s in (research or {}).get(lang, {}).get("serps", []):
        paa += s.get("paa", [])
    if paa:
        out.append("\n## Що питають у Google (можна використати у відповідях)\n" + " | ".join(dict.fromkeys(paa)))
    return "\n".join(out)


# ---------------------------------------------------------------- перевірки
def strip_md(md: str) -> str:
    t = re.sub(r"^#{1,6}\s*", "", md, flags=re.M)
    t = re.sub(r"^\|?\s*-{2,}.*$", "", t, flags=re.M)
    t = t.replace("**", "").replace("|", " ")
    return re.sub(r"\s+", " ", t).strip()


def _range(f: str):
    return norms.parse_range(f)


def _count(text: str, k: str) -> int:
    return norms.count(text, k)


def headings(md: str):
    return [(len(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^(#{1,3})\s+(.+)$", md, re.M)]


def _norm_h(s):
    return re.sub(r"[^\wʼ'’]+", " ", s.lower()).strip()


def section_targets(tz: dict, lang: str):
    """[(заголовок H1/H2/CTA/FAQ, lo, hi)] — цільовий обсяг розділу разом з його H3, з коментарів ТЗ."""
    from .tz_import import estimate_volume
    L = 0 if lang == "ru" else 1
    groups, cur = [], None
    for b in tz["blocks"]:
        if b[0] in ("h1", "h2", "cta", "faq"):
            title = b[1 + L] if b[0] in ("h1", "h2") else b[0].upper()
            cur = [title, []]
            groups.append(cur)
        elif cur is not None:
            cur[1].append(b)
    out = []
    for title, bl in groups:
        r = norms.parse_range(estimate_volume(bl).replace(" ", "")) if estimate_volume(bl) else None
        if title == "FAQ":
            nq = sum(1 for b in bl if b[0] == "q")
            r = (250 * nq, 400 * nq) if nq else r
        out.append((title, r[0] if r else None, r[1] if r else None))
    return out


def section_sizes(md: str):
    """{нормалізований заголовок H1/H2: обсяг розділу з підрозділами}."""
    parts = re.split(r"^(#{1,2})\s+(.+)$", md, flags=re.M)
    out = {}
    for i in range(1, len(parts) - 1, 3):
        out[_norm_h(parts[i + 1])] = len(strip_md(parts[i + 1] + " " + parts[i + 2]))
    return out


def section_text(md: str, title: str):
    """(початок, кінець) розділу за заголовком H1/H2 — до наступного H1/H2."""
    ms = list(re.finditer(r"^(#{1,2})\s+(.+)$", md, re.M))
    for j, m in enumerate(ms):
        if _norm_h(m.group(2)) == _norm_h(title):
            return m.start(), (ms[j + 1].start() if j + 1 < len(ms) else len(md))
    return None


def check(md: str, tz: dict, lang: str) -> dict:
    L = 0 if lang == "ru" else 1
    issues, warns = [], []
    plain = strip_md(md)
    n = len(plain)
    vr = norms.volume_range(tz)
    vol_bad = False
    if vr:
        lo, hi = vr
        if n < lo * 0.97 or n > hi * 1.03:
            vol_bad = True
            issues.append(f"Загальний обсяг {n} зн., треба {lo}–{hi} зн. ({'скоротити' if n > hi else 'додати'} ≈ {abs(n - (hi if n > hi else lo))} зн.)")
    # розділи
    sizes, sections = section_sizes(md), []
    for title, lo, hi in section_targets(tz, lang):
        act = sizes.get(_norm_h(title))
        sections.append((title, act, lo, hi))
        if vol_bad and act and lo and hi and (act > hi * 1.15 or act < lo * 0.85):
            issues.append(f"Розділ «{title}»: {act} зн., ціль {lo}–{hi}")
    # заголовки
    want = []
    for b in tz["blocks"]:
        if b[0] in ("h1", "h2", "h3", "q"):
            want.append(b[1 + L].strip())
        elif b[0] in ("cta", "faq"):
            want.append(b[0].upper())
    hn = [_norm_h(h) for _, h in headings(md)]
    pos = -1
    for w in want:
        try:
            pos = hn.index(_norm_h(w), pos + 1)
        except ValueError:
            issues.append(f"Немає заголовка (або порушено порядок): «{w}»")
    # ключі та LSI — за ефективними нормами
    kw_rep = []
    for kind, k, lo, hi, f, in_head, note in norms.effective(tz, lang):
        total = _count(plain, k)
        shown = f if not note else f"{lo}-{hi} ({note})"
        kw_rep.append((kind, k, total, shown))
        if note:
            warns.append(f"{kind} «{k}»: {note}")
        if total > hi:
            issues.append(f"{kind} «{k}»: {total} входжень, норма {lo}–{hi} — прибрати щонайменше {total - hi} (у заголовках {in_head}, їх не чіпати)")
        elif total < lo:
            (issues if kind == "ключ" else warns).append(f"{kind} «{k}»: {total} входжень, норма {lo}–{hi} — додати {lo - total}")
    # FAQ
    faq = re.split(r"^##\s+FAQ\s*$", md, flags=re.M | re.I)
    if len(faq) > 1:
        parts = re.split(r"^###\s+.+$", faq[1], flags=re.M)[1:]
        for i, p in enumerate(parts, 1):
            ln = len(strip_md(p))
            if ln < 220 or ln > 450:
                issues.append(f"Відповідь FAQ №{i}: {ln} зн., треба 250–400")
    if re.search(r"[\U0001F300-\U0001FAFF]", md):
        issues.append("У тексті є емодзі")
    for comp in tz.get("competitors", []):
        dom = comp.split("/")[0].replace("www.", "")
        if dom and dom.split(".")[0] in plain.lower() and len(dom.split(".")[0]) > 3:
            issues.append(f"Згадка конкурента: {dom}")
    placeholders = re.findall(r"\[(?:уточн|уточнить|уточнити)[^\]]*\]", md, re.I)
    return {"chars": n, "issues": issues, "warnings": warns, "keywords": kw_rep, "sections": sections,
            "placeholders": placeholders, "ok": not issues}


async def languagetool(md: str, lang: str) -> list[str]:
    if not config.LANGUAGETOOL_URL or config.MOCK:
        return []
    text = strip_md(md)
    found = []
    try:
        async with httpx.AsyncClient(timeout=60) as cl:
            for i in range(0, len(text), 8000):  # публічний API: до 20 КБ на запит
                chunk = text[i:i + 8000]
                r = await cl.post(config.LANGUAGETOOL_URL, data={"text": chunk, "language": LT_LANG[lang]})
                if r.status_code != 200:
                    break
                for m in r.json().get("matches", []):
                    cat = (m.get("rule", {}).get("category", {}) or {}).get("id", "")
                    if cat in ("TYPOGRAPHY", "STYLE", "REDUNDANCY") and m.get("rule", {}).get("issueType") != "misspelling":
                        continue
                    frag = chunk[max(0, m["offset"] - 25): m["offset"] + m["length"] + 25]
                    sug = ", ".join(x["value"] for x in m.get("replacements", [])[:3])
                    found.append(f"«…{frag}…» — {m.get('message')}" + (f" (варіанти: {sug})" if sug else ""))
    except Exception:  # noqa: BLE001
        return found
    return found[:40]


# ---------------------------------------------------------------- генерація
def _clean_md(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", s)
    return s.strip()


SECTION_SYS = """Ти — редактор. Перепиши ОДИН розділ SEO-тексту так, щоб його обсяг (знаки з пробілами, без розмітки) потрапив у ціль.
Збережи заголовки розділу й підрозділів дослівно, зміст, факти, плейсхолдери [уточнити], таблиці/списки й ключові слова з переліку. Скорочуй «воду» і повтори, а не суть; якщо треба додати — додай конкретику. Мова — природна {lang_name}, без помилок. Поверни лише markdown цього розділу."""


async def fix_sections(md, tz, lang, brief_kw, log):
    """Точкове виправлення обсягу: переписуємо найбільш відхилені розділи з чіткою ціллю."""
    import asyncio
    rep = check(md, tz, lang)
    vr = norms.volume_range(tz)
    if not vr:
        return md
    n = rep["chars"]
    if vr[0] * 0.97 <= n <= vr[1] * 1.03:
        return md
    cand = []
    for title, act, lo, hi in rep["sections"]:
        if not (act and lo and hi):
            continue
        dev = act - hi if n > vr[1] else lo - act
        if dev > 80:
            cand.append((dev, title, act, lo, hi))
    cand.sort(reverse=True)
    need = n - vr[1] if n > vr[1] else vr[0] - n
    for dev, title, act, lo, hi in cand[:5]:
        if need <= 0:
            break
        span = section_text(md, title)
        if not span:
            continue
        target = (lo + hi) // 2
        log(f"{lang.upper()}: підганяю розділ «{title[:40]}» {act} → ~{target} зн.")
        part = md[span[0]:span[1]]
        new = _clean_md(await asyncio.to_thread(
            llm.call_text, SECTION_SYS.replace("{lang_name}", LANG_NAME[lang]),
            f"Ціль: {lo}–{hi} зн. (зараз {act}).\nКлючові слова, які треба зберегти, якщо вони є в розділі: {brief_kw}\n\n## Розділ\n{part}", 6000))
        if headings(new) and _norm_h(headings(new)[0][1]) == _norm_h(title):
            md = md[:span[0]] + new.strip() + "\n\n" + md[span[1]:].lstrip()
            need -= abs(act - len(strip_md(new)))
    return md


async def write_text(tz: dict, lang: str, project: dict, research: dict | None, log) -> dict:
    import asyncio
    brief = tz_brief(tz, lang, project, research)
    if config.MOCK:
        md = _mock_text(tz, lang)
    else:
        log(f"{lang.upper()}: Claude пише текст")
        md = _clean_md(await asyncio.to_thread(
            llm.call_text, WRITER_SYS.replace("{lang_name}", LANG_NAME[lang]), brief + "\n\nНапиши текст сторінки.", 16000))
    rounds = 0
    while True:
        rep = check(md, tz, lang)
        spell = await languagetool(md, lang)
        log(f"{lang.upper()}: перевірка — {len(rep['issues'])} проблем, {len(spell)} зауважень LanguageTool, {rep['chars']} зн.")
        if (rep["ok"] and not spell) or rounds >= config.TEXT_MAX_FIX_ROUNDS or config.MOCK:
            break
        rounds += 1
        if any(x.startswith("Загальний обсяг") for x in rep["issues"]):
            md = await fix_sections(md, tz, lang, ", ".join(k for k, _ in tz.get(f"kw_{lang}", [])), log)
            rep = check(md, tz, lang)
        sec = [f"- «{t}»: {a} зн. (ціль {lo}–{hi})" for t, a, lo, hi in rep["sections"] if a and lo]
        report = "\n".join(["## Звіт перевірки", *[f"- {x}" for x in rep["issues"]],
                            *(["\n## Обсяги розділів"] + sec if sec else []),
                            *(["\n## Зауваження LanguageTool (перевір, виправ справжні помилки)"] + [f"- {x}" for x in spell] if spell else []),
                            "\n## Фактичні входження ключів/LSI", *[f"- {k[0]} «{k[1]}»: {k[2]} (норма {k[3]})" for k in rep["keywords"]],
                            "\nВажливо: заголовки не змінюй; кількість входжень зменшуй синонімами, займенниками й перебудовою речень; обсяг розділів тримай у цілях."])
        log(f"{lang.upper()}: редактор виправляє (раунд {rounds})")
        md = _clean_md(await asyncio.to_thread(
            llm.call_text, EDITOR_SYS.replace("{lang_name}", LANG_NAME[lang]),
            f"{brief}\n\n{report}\n\n## Текст\n{md}\n\nПоверни виправлений повний текст.", 16000))
    # фінальна вичитка: окремий прохід коректора без зміни структури (лише якщо ще не редагували)
    if rounds == 0 and not config.MOCK:
        log(f"{lang.upper()}: фінальна коректура")
        fixed = _clean_md(await asyncio.to_thread(
            llm.call_text, EDITOR_SYS.replace("{lang_name}", LANG_NAME[lang]),
            f"{brief}\n\n## Звіт перевірки\n- Автоматичних проблем не знайдено. Зроби лише коректуру: орфографія, пунктуація, граматика, стиль, логіка. Нічого не скорочуй і не додавай.\n\n## Текст\n{md}", 16000))
        if check(fixed, tz, lang)["ok"]:
            md = fixed
    if not config.MOCK and not check(md, tz, lang)["ok"] and any(x.startswith("Загальний обсяг") for x in check(md, tz, lang)["issues"]):
        md = await fix_sections(md, tz, lang, ", ".join(k for k, _ in tz.get(f"kw_{lang}", [])), log)
        rep = check(md, tz, lang)
        spell = await languagetool(md, lang)
    rep = check(md, tz, lang)
    return {"lang": lang, "markdown": md, "report": rep, "spelling": spell, "rounds": rounds}


def _mock_text(tz, lang):
    L = 0 if lang == "ru" else 1
    lines = []
    for b in tz["blocks"]:
        t = b[0]
        if t == "h1":
            lines.append(f"# {b[1 + L]}\n\nТестовий абзац.")
        elif t in ("h2", "h3"):
            lines.append(f"{'##' if t == 'h2' else '###'} {b[1 + L]}\n\nТестовий текст розділу.")
        elif t in ("cta", "faq"):
            lines.append(f"## {t.upper()}\n\nТекст.")
        elif t == "q":
            lines.append(f"### {b[1 + L]}\n\n" + "Відповідь на питання. " * 15)
    return "\n\n".join(lines)
