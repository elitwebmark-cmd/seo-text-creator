"""Написання SEO-тексту за ТЗ: автор → автоматична перевірка → редактор (до N раундів)."""
import re
from pathlib import Path

import httpx

from . import config, llm
from .analysis import phrase_regex

REFERENCE = (Path(__file__).parent / "examples" / "text_serm_ua.md").read_text(encoding="utf-8")
LANG_NAME = {"ua": "українською", "ru": "російською"}
LT_LANG = {"ua": "uk-UA", "ru": "ru-RU"}

WRITER_SYS = """Ти — досвідчений SEO-копірайтер і редактор digital-агенції. Пишеш продаючі, експертні SEO-тексти для сторінок послуг суворо за технічним завданням (ТЗ).

ПРАВИЛА
1. Структура — точно за ТЗ: усі H1/H2/H3 дослівно, у тому самому порядку, нічого не додавай і не прибирай. «CTA» і «FAQ» — це службові розділи (## CTA, ## FAQ).
2. Обсяг кожного блоку — за коментарем ТЗ (±15%). Загальний обсяг — строго в межах, указаних у ТЗ (знаки з пробілами, без розмітки).
3. Виконай усе, що просить коментар: таблиці, нумеровані етапи, списки, формули з прикладом, порівняння.
4. Ключі — у межах рекомендованої кількості входжень на весь текст (разом із заголовками). Головний ключ — у першому абзаці. Не став два ключі в одне речення. LSI — у межах діапазону, не перевищуй верхню межу. Чергуй синоніми, без переспаму.
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
Якщо треба скоротити — прибирай «воду», а не зміст. Якщо треба додати — додавай корисну конкретику, а не повтори."""


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
    out.append("\n## Ключові запити (кількість входжень на весь текст)")
    out += [f"- {k}: {f}" for k, f in tz.get(f"kw_{lang}", [])]
    out.append("\n## LSI (діапазон входжень)")
    out += [f"- {k}: {f}" for k, f in tz.get(f"lsi_{lang}", [])]
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
    nums = [int(x) for x in re.findall(r"\d+", str(f).split("(")[0])]
    if not nums:
        nums = [int(x) for x in re.findall(r"\d+", str(f))]
    if not nums:
        return None
    return (nums[0], nums[1]) if len(nums) > 1 else (nums[0], nums[0])


def _alts(k: str):
    k = re.sub(r"\(.*?\)", "", k)
    return [a.strip() for a in re.split(r"\s+/\s+|/", k) if a.strip()]


def _count(text: str, k: str) -> int:
    """Входження будь-якої з альтернатив; перекриття («бренд» у «брендові запити») рахуються один раз."""
    spans = sorted(m.span() for a in _alts(k) for m in phrase_regex(a).finditer(text or ""))
    n, end = 0, -1
    for s0, s1 in spans:
        if s0 >= end:
            n += 1
            end = s1
        else:
            end = max(end, s1)
    return n


def headings(md: str):
    return [(len(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^(#{1,3})\s+(.+)$", md, re.M)]


def check(md: str, tz: dict, lang: str) -> dict:
    L = 0 if lang == "ru" else 1
    issues, warns = [], []
    plain = strip_md(md)
    n = len(plain)
    vr = _range(str(tz.get("volume", "")).replace(" ", "").replace(" ", ""))
    if vr:
        lo, hi = vr
        if n < lo * 0.97 or n > hi * 1.03:
            issues.append(f"Загальний обсяг {n} зн., треба {lo}–{hi} зн.")
    # заголовки
    want = []
    for b in tz["blocks"]:
        if b[0] in ("h1", "h2", "h3", "q"):
            want.append(b[1 + L].strip())
        elif b[0] in ("cta", "faq"):
            want.append(b[0].upper())
    have = [h for _, h in headings(md)]
    norm = lambda s: re.sub(r"[^\wʼ'’]+", " ", s.lower()).strip()  # noqa: E731
    hn = [norm(h) for h in have]
    pos = -1
    for w in want:
        try:
            i = hn.index(norm(w), pos + 1)
            pos = i
        except ValueError:
            issues.append(f"Немає заголовка (або порушено порядок): «{w}»")
    # ключі та LSI
    head_text = " ".join(have)
    body = strip_md(re.sub(r"^#{1,3}\s+.+$", "", md, flags=re.M))
    kw_rep = []
    for kind, items in (("ключ", tz.get(f"kw_{lang}", [])), ("LSI", tz.get(f"lsi_{lang}", []))):
        for k, f in items:
            r = _range(f)
            if not r:
                continue
            total, in_body = _count(plain, k), _count(body, k)
            kw_rep.append((kind, k, total, f))
            lo, hi = r
            if total > hi:
                if in_body <= max(0, hi - (total - in_body)) or in_body <= 1:
                    warns.append(f"{kind} «{k}»: {total} (норма {f}) — перевищення через заголовки ТЗ")
                else:
                    issues.append(f"{kind} «{k}»: {total} входжень, норма {f} — зменшити в тексті (у заголовках {total - in_body})")
            elif total < lo:
                (issues if kind == "ключ" else warns).append(f"{kind} «{k}»: {total} входжень, норма {f} — додати")
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
    return {"chars": n, "issues": issues, "warnings": warns, "keywords": kw_rep, "placeholders": placeholders, "ok": not issues}


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
        report = "\n".join(["## Звіт перевірки", *[f"- {x}" for x in rep["issues"]],
                            *(["\n## Зауваження LanguageTool (перевір, виправ справжні помилки)"] + [f"- {x}" for x in spell] if spell else []),
                            "\n## Фактичні входження ключів/LSI", *[f"- {k[0]} «{k[1]}»: {k[2]} (норма {k[3]})" for k in rep["keywords"]]])
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
        rep = check(md, tz, lang)
        spell = await languagetool(md, lang)
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
