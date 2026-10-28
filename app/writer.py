"""Написання SEO-тексту за ТЗ: автор → автоматична перевірка → редактор (до N раундів)."""
import re
from pathlib import Path

import httpx

from . import config, llm
from . import norms, styles, text_types

REFERENCE = (Path(__file__).parent / "examples" / "text_serm_ua.md").read_text(encoding="utf-8")
LANG_NAME = {"ua": "українською", "ru": "російською"}
LT_LANG = {"ua": "uk-UA", "ru": "ru-RU"}

WRITER_SYS = """Ти — досвідчений SEO-копірайтер і редактор digital-агенції. Пишеш продаючі, експертні SEO-тексти для сторінок послуг суворо за технічним завданням (ТЗ).

ПРАВИЛА
1. Структура — точно за ТЗ: усі H1/H2/H3 дослівно, у тому самому порядку, нічого не додавай і не прибирай. «CTA» і «FAQ» — це службові розділи (## CTA, ## FAQ). Винятки для статей — у розділі «Формат тексту» (блоки коротких H3 → список; у медіа CTA немає, а перед FAQ — фінальний розділ).
2. Обсяг кожного блоку — за коментарем ТЗ (±15%). Загальний обсяг — строго в межах, указаних у ТЗ (знаки з пробілами, без розмітки).
3. Виконай усе, що просить коментар: таблиці, нумеровані етапи, списки, формули з прикладом, порівняння.
4. Ключі — у межах рекомендованої кількості входжень на весь текст (разом із заголовками). Головний ключ — у першому абзаці (у статтях — природно в першому-другому абзаці). Не став два ключі в одне речення. LSI — у межах діапазону, не перевищуй верхню межу. Чергуй синоніми, без переспаму.
   Пріоритети, якщо вимоги конфліктують: структура → загальний обсяг → ключі → LSI (нижні межі LSI — бажані, верхні — обов’язкові).
5. Мова: пиши {lang_name}, природно, як носій мови; без кальок, русизмів/суржику (для UA), без канцеляризмів і «води». Довжина абзаців і кількість списків — за розділом «Формат тексту» з ТЗ. Тон — експертний, від імені компанії («ми»), звернення до читача на «ви»/«вы». Якщо в ТЗ є розділ «Стиль і тональність» — тон, ритм, лексику і прийоми бери з нього (він важливіший за тон еталона), але вимоги ТЗ (структура, обсяги, ключі) не порушуй.
6. ГРАМОТНІСТЬ: жодних орфографічних, пунктуаційних, граматичних, стилістичних і смислових помилок. Узгодження слів, правильні відмінки, апострофи (UA), лапки «ялинки», тире — між частинами речення.
7. ФАКТИ І ЦИФРИ: про компанію — лише факти з блоку «Про компанію». Статистика ринку, досліджень, платформ — ЛИШЕ з розділу «Перевірені факти», з посиланням на джерело (markdown-посилання). Інших статистичних цифр, відсотків і «даних досліджень» з пам'яті НЕ пиши. Дозволені також числа з ТЗ, з «Про компанію» та умовні розрахунки з позначкою «умовно» / «для прикладу». НЕ вигадуй ціни, кейси, цифри клієнта, імена, сертифікати, партнерства, гарантії, терміни, нові зобов’язання. Ціни ринку й назви конкурентів у текст не переносити.
   ТЕКСТ ГОТОВИЙ ДО ПУБЛІКАЦІЇ: жодних плейсхолдерів, квадратних дужок «[уточнити…]», TODO, «XX». Якщо ТЗ просить даних, яких немає (ціни, терміни, кейси, кількість клієнтів), напиши цей фрагмент без конкретних чисел, але корисно й чесно: від чого залежить вартість, що входить у роботу, як визначаються терміни, що клієнт отримає на виході.
8. CTA: текст за коментарем, потім окремим рядком кнопка: **[Текст кнопки]**. (У статтях для медіа CTA немає — див. «Рамка медіа».)
9. FAQ: кожне питання — ### H3, відповідь 250–400 знаків, по суті, без повтору тексту вище.
10. Без емодзі. Таблиці — у markdown. Жодних заголовків, яких немає в ТЗ, крім фінального розділу медіа-статті (структуру всередині розділу роби абзацами, а списками й таблицями — так, як велить розділ «Формат тексту»).
11. Посилання — лише на URL із «Перевірених фактів», у форматі [назва джерела](URL).

ФОРМАТ ВІДПОВІДІ: лише markdown-текст сторінки, без пояснень. # — H1, ## — H2 (а також «## CTA», «## FAQ»), ### — H3 і питання FAQ.

"""
REF_BLOCK = """
ЕТАЛОН РІВНЯ (інша сторінка, інша компанія — НЕ копіюй факти, бери лише рівень якості, тон, щільність і форматування):
""" + REFERENCE


def writer_system(lang: str, ttype: str = "landing") -> str:
    """Спільний system для автора, редактора й коректури — разом із брифом кешується (prompt caching)."""
    return WRITER_SYS.replace("{lang_name}", LANG_NAME[lang]) + ("" if text_types.is_article(ttype) else REF_BLOCK)

EDITOR_SYS = """ЗАВДАННЯ: ти — шеф-редактор і коректор. Нижче — звіт перевірки (автоматика + аудит відповідності ТЗ) і поточний текст. Виправ текст ПРАВКАМИ.
ЯК ПРАЦЮВАТИ:
- Змінюй лише речення й абзаци, яких стосуються пункти звіту. Решту тексту не чіпай.
- Виправ УСІ пункти звіту: обсяг, заголовки, ключі/LSI, FAQ, невиконані вимоги коментарів ТЗ, смислові й фактичні помилки, неперевірені цифри, плейсхолдери, орфографію.
- Структура: усі заголовки ТЗ дослівно і в тому самому порядку; заголовків, яких немає в ТЗ, бути не повинно (винятки для статей — у «Формат тексту»: короткі блоки H3 → список із жирними назвами пунктів; у медіа — фінальний розділ перед FAQ замість CTA).
- Для статей виконуй «Правила редакції»: без риторичних питань, «Так/Ні» на початку відповідей, жирного в абзацах, більше одного тире в реченні, слів-переходів на початку абзаців поспіль, цифр і термінів без джерела.
- Цифри статистики — лише з «Перевірених фактів» і з посиланням; неперевірену цифру прибери (переформулюй думку без числа). Факти про компанію — лише з «Про компанію». Жодних плейсхолдерів «[уточнити…]».
- Посилання [джерело](URL) і кнопку CTA **[…]** збережи. Мова — природна, без помилок, суржику й кальок; стиль — за розділом «Стиль і тональність».
- Формат — за розділом «Формат тексту». Якщо абзаци закороткі або забагато списків (стаття) — переформатуй такий розділ цілком (див. нижче, як замінити розділ).
- Пункт LanguageTool, що є хибною тривогою (термін, бренд), пропускай.
Якщо треба скоротити — прибирай «воду», а не зміст. Якщо треба додати — корисну конкретику, а не повтори.
Пріоритети: структура і вимоги ТЗ → факти і смисл → обсяг → ключі → верхні межі LSI → нижні межі LSI.

ФОРМАТ ВІДПОВІДІ — ЛИШЕ ПРАВКИ, без пояснень і БЕЗ повного тексту. Кожна правка:
<<<<<<< FIND
фрагмент поточного тексту — ДОСЛІВНО, символ у символ (ціле речення, абзац або розділ; достатньо довгий, щоб бути унікальним)
=======
новий варіант цього фрагмента
>>>>>>> END
- Щоб переписати розділ ЦІЛКОМ (напр. переформатувати абзаци), НЕ повторюй старий текст — використай заміну розділу:
<<<<<<< SECTION
## Точний рядок заголовка розділу (як у тексті)
новий повний текст розділу (без рядка заголовка)
>>>>>>> END
  Розділ — від заголовка до наступного заголовка того ж або вищого рівня.
- Щоб видалити фрагмент — залиш нову частину порожньою. Щоб додати текст — візьми в FIND сусіднє речення і поверни його разом із доданим.
- Правки не повинні перекриватися. Якщо виправляти нічого — відповідай одним рядком: БЕЗ ЗМІН"""


PATCH_RE = re.compile(r"<{5,}\s*FIND[^\n]*\n(.*?)\n={5,}[^\n]*\n(.*?)\n?>{5,}[^\n]*", re.S)


def _flex(old: str, md: str):
    """Позиція фрагмента з точністю до пробілів/переносів: (start, end) або None, якщо 0 чи >1 збігів."""
    if md.count(old) == 1:
        i = md.index(old)
        return i, i + len(old)
    toks = old.split()
    if not toks:
        return None
    rx = re.compile(r"\s+".join(re.escape(t) for t in toks))
    ms = list(rx.finditer(md))
    return (ms[0].start(), ms[0].end()) if len(ms) == 1 else None


SECTION_PATCH_RE = re.compile(r"<{5,}\s*SECTION[^\n]*\n(#{1,3}[^\n]+)\n(.*?)\n?>{5,}[^\n]*", re.S)


def apply_patches(md: str, answer: str):
    """(новий текст, застосовано, не знайдено). Незавершена остання правка (обрізана відповідь) ігнорується."""
    ok = bad = 0
    for head, body in SECTION_PATCH_RE.findall(answer or ""):
        title = re.sub(r"^#{1,3}\s+", "", head.strip())
        span_body = section_body(md, title)
        ms = [m for m in re.finditer(r"^(#{1,3})\s+(.+)$", md, re.M) if _norm_h(m.group(2)) == _norm_h(title)]
        if span_body is None or len(ms) != 1:
            bad += 1
            continue
        start = ms[0].end()
        md = md[:start] + "\n\n" + body.strip("\n") + "\n\n" + md[start + len(span_body):].lstrip("\n")
        ok += 1
    for old, new in PATCH_RE.findall(answer or ""):
        old = old.strip("\n")
        span = _flex(old, md) if old.strip() else None
        if not span:
            bad += 1
            continue
        md = md[:span[0]] + new.strip("\n") + md[span[1]:]
        ok += 1
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md, ok, bad


# ---------------------------------------------------------------- ТЗ → текст для промпту
def tz_brief(tz: dict, lang: str, project: dict, research: dict | None = None, style: str = "default", facts: list | None = None) -> str:
    L = 0 if lang == "ru" else 1
    out = [f"## ТЗ ({lang.upper()}-версія): {tz.get('name_' + lang) or tz.get('name_ua')}",
           f"Загальний обсяг: {tz.get('volume')}",
           f"Шаблон: {tz.get('template')}"]
    ttype = text_types.resolve(project)
    out.append(f"Тип тексту: {text_types.TYPES[ttype]}")
    sb = styles.brief_block(style, lang, ttype)
    if sb:
        out.append("\n" + sb)
    out.append("\n" + text_types.format_block(ttype))
    if project.get("facts") and ttype != text_types.MEDIA:
        out.append(f"\n## Про компанію (лише ці факти можна використовувати)\n{project['facts']}")
    if project.get("domain") and ttype != text_types.MEDIA:
        out.append(f"Сайт: {project['domain']}")
    if project.get("notes"):
        out.append(f"Додаткові вимоги: {project['notes']}")
    if facts is not None:
        from . import facts as _facts
        out.append("\n" + _facts.brief_block(facts, lang, style))
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
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def strip_md(md: str) -> str:
    t = LINK_RE.sub(r"\1", md)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
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


PLACEHOLDER_RE = re.compile(r"\[(?:\s*(?:уточн|уточнить|уточните|вставити|вставить|вказати|указать|додати|добавить|todo|tbd|xx+|дані|данные|цифр|назва|название|джерело|источник|посилання|ссылка)[^\]]*|\s*\.\.\.\s*|\s*…\s*)\](?!\()", re.I)
STAT_RE = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|відсот\w*|процент\w*|млн|млрд|тис\.|тыс\.|тисяч\w*|тысяч\w*|разів|рази|раза|мільйон\w*|миллион\w*|мільярд\w*|миллиард\w*)", re.I)
SOURCE_HINT_RE = re.compile(r"за даними|за дослідженням|згідно з (?:дослідж|даними|опитуванн)|опитуванн|дослідженн|по данным|согласно (?:исследов|данным|опрос)|опрос|исследовани|статистик", re.I)
VAGUE_RE = re.compile(r"за даними (?:досліджень|експертів|статистики|аналітиків)|за статистикою|експерти вважають|дослідження показують|по данным (?:исследований|экспертов|статистики|аналитиков)|по статистике|эксперты считают|исследования показывают", re.I)
ILLUSTR_RE = re.compile(r"умовн|припуст|приклад|наприклад|условн|допуст|пример|например|формул|[=×÷]|\d\s*[/+*]\s*\d", re.I)
REQ_TABLE = re.compile(r"таблиц", re.I)
REQ_STEPS = re.compile(r"нумерован|покроков|по кроках|по шагам|пошагов|етап(?:и|ів)\b|этап(?:ы|ов)\b|кроки\b|шаги\b", re.I)
REQ_LIST = re.compile(r"списк|список|перелік|перечень|маркован|буллет", re.I)
NEG_RE = re.compile(r"без (?:таблиц|списк|переліку|нумерац)", re.I)


def _num_key(s: str) -> str:
    m = re.search(r"\d+(?:[.,]\d+)?", s.replace("\u00a0", " ").replace(" ", ""))
    return m.group(0).replace(",", ".") if m else ""


def _sentences(text: str):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text) if x.strip()]


def tz_sections(tz: dict, lang: str):
    """[(рівень, заголовок, [коментарі])] — заголовки H1/H2/H3 з коментарями під ними."""
    L = 0 if lang == "ru" else 1
    out = []
    for b in tz["blocks"]:
        if b[0] in ("h1", "h2", "h3"):
            out.append((int(b[0][1]), b[1 + L], []))
        elif b[0] in ("cta", "faq"):
            out.append((2, b[0].upper(), []))
        elif b[0] == "c" and out:
            out[-1][2].append(b[1])
    return out


def section_body(md: str, title: str) -> str | None:
    """Текст розділу (будь-якого рівня) — до наступного заголовка того ж або вищого рівня."""
    ms = list(re.finditer(r"^(#{1,3})\s+(.+)$", md, re.M))
    for j, m in enumerate(ms):
        if _norm_h(m.group(2)) == _norm_h(title):
            lvl = len(m.group(1))
            end = next((x.start() for x in ms[j + 1:] if len(x.group(1)) <= lvl), len(md))
            return md[m.end():end]
    return None


def fact_checks(md: str, tz: dict, lang: str, facts: list | None, project: dict | None):
    """Цифри, джерела, посилання, плейсхолдери."""
    issues, warns = [], []
    facts = facts or []
    project = project or {}
    for m in PLACEHOLDER_RE.finditer(md):
        issues.append(f"Плейсхолдер «{m.group(0)}» — текст має бути готовим до публікації: переписати фрагмент без нього")
    fact_urls = {f["url"].rstrip("/") for f in facts}
    own = (project.get("domain") or "").replace("https://", "").replace("http://", "").strip("/").lower()
    for m in LINK_RE.finditer(md):
        u = m.group(2).rstrip("/")
        if u not in fact_urls and not (own and own in u.lower()):
            issues.append(f"Посилання не з перевірених джерел: {m.group(2)} — прибрати")
    allowed = " ".join([project.get("facts", ""), project.get("notes", ""), str(tz.get("volume", ""))]
                       + [b[1] if len(b) < 3 else f"{b[1]} {b[2]}" for b in tz["blocks"] if len(b) > 1])
    allowed_keys = {_num_key(x.group(0)) for x in re.finditer(r"\d+(?:[.,]\d+)?", allowed.replace("\u00a0", ""))}
    fact_keys = {}
    for f in facts:
        for x in re.finditer(r"\d+(?:[.,]\d+)?", (f.get("number", "") + " " + f.get("quote", "")).replace("\u00a0", "")):
            fact_keys.setdefault(_num_key(x.group(0)), f)
    plain_links = LINK_RE.sub(lambda m: f"{m.group(1)} <{m.group(2)}>", md)
    plain_links = re.sub(r"^#{1,6}\s*", "", plain_links, flags=re.M).replace("**", "")
    used = set()
    for sent in _sentences(plain_links):
        if VAGUE_RE.search(sent) and "<http" not in sent:
            issues.append(f"Джерело не назване: «{sent[:140]}» — назвати джерело з «Перевірених фактів» або прибрати")
        stats = list(STAT_RE.finditer(sent))
        if not stats and SOURCE_HINT_RE.search(sent):
            stats = [x for x in re.finditer(r"\d+(?:[.,]\d+)?", sent) if not re.fullmatch(r"(19|20)\d\d", x.group(0))]
        for st in stats:
            k = _num_key(st.group(0))
            if not k:
                continue
            if k in fact_keys:
                f = fact_keys[k]
                used.add(f["id"])
                if f["url"].rstrip("/") not in sent:
                    issues.append(f"Факт {f['id']} ({f['number']}) використано без посилання на джерело {f['url']}")
                continue
            if k in allowed_keys or k == "100" or ILLUSTR_RE.search(sent):
                continue
            issues.append(f"Цифра без перевіреного джерела: «{sent[:160]}» — прибрати число або взяти факт із «Перевірених фактів»")
            break
    return issues, warns, sorted(used)


def structure_checks(md: str, tz: dict, lang: str, ttype: str = "landing"):
    """Зайві заголовки, H1, головний ключ, вимоги коментарів (таблиця / етапи / список)."""
    issues, warns = [], []
    article = text_types.is_article(ttype)
    secs = tz_sections(tz, lang)
    known = {_norm_h(t) for _, t, _ in secs} | {_norm_h(b[1 + (0 if lang == "ru" else 1)]) for b in tz["blocks"] if b[0] == "q"}
    hs = headings(md)
    if sum(1 for lvl, _ in hs if lvl == 1) != 1:
        issues.append("У тексті має бути рівно один H1")
    body_h2 = [h for lvl, h in hs if lvl == 2 and h.strip().upper() not in ("FAQ", "CTA")]
    final_h2 = body_h2[-1] if (ttype == text_types.MEDIA and body_h2) else None
    for lvl, h in hs:
        if h == final_h2 and lvl == 2:
            continue  # фінальний розділ медіа-статті може бути відсутнім у ТЗ
        if _norm_h(h) not in known:
            (issues if lvl <= 2 else warns).append(f"Заголовок H{lvl} «{h}» відсутній у ТЗ — прибрати (зробити абзацом / жирним рядком)")
    if re.search(r"^#{4,}\s", md, re.M):
        issues.append("Заголовки H4+ не передбачені ТЗ")
    kws = tz.get(f"kw_{lang}") or []
    if kws:
        body = re.split(r"^#\s+.+$", md, maxsplit=1, flags=re.M)
        paras = [p for p in re.split(r"\n\s*\n", body[-1]) if p.strip() and not p.lstrip().startswith("#")]
        first = " ".join(paras[:2] if article else paras[:1])
        if first and not _count(strip_md(first), kws[0][0]):
            if article:
                warns.append(f"Головний ключ «{kws[0][0]}» бажано природно згадати в перших двох абзацах")
            else:
                issues.append(f"Головний ключ «{kws[0][0]}» має бути в першому абзаці")
    for lvl, title, comments in secs:
        c = " ".join(comments)
        if not c or NEG_RE.search(c):
            continue
        body = section_body(md, title)
        if body is None:
            continue
        if REQ_TABLE.search(c) and not re.search(r"^\s*\|.+\|", body, re.M):
            issues.append(f"Розділ «{title}»: ТЗ вимагає таблицю — її немає")
        if REQ_STEPS.search(c) and not re.search(r"^\s*\d+[.)]\s", body, re.M) and not re.search(r"^###\s", body, re.M):
            warns.append(f"Розділ «{title}»: ТЗ описує етапи/кроки — бажано нумерований список")
        if REQ_LIST.search(c) and not re.search(r"^\s*(?:[-*•]|\d+[.)])\s", body, re.M) and not re.search(r"^\s*\|", body, re.M):
            warns.append(f"Розділ «{title}»: ТЗ просить список — його немає")
    return issues, warns


def check(md: str, tz: dict, lang: str, style: str = "default", facts: list | None = None, project: dict | None = None) -> dict:
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
    ttype = text_types.resolve(project)
    article = text_types.is_article(ttype)
    want, parent = [], None
    for b in tz["blocks"]:
        if b[0] in ("h1", "h2", "h3", "q"):
            want.append((b[1 + L].strip(), b[0], parent))
            if b[0] in ("h1", "h2"):
                parent = b[1 + L].strip()
        elif b[0] in ("cta", "faq"):
            want.append((b[0].upper(), b[0], parent))
            parent = b[0].upper()
    seq = []  # (нормалізований текст, 'h' — заголовок | 'lead' — жирна назва пункту списку)
    for line in md.splitlines():
        m = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
        if m:
            seq.append((_norm_h(m.group(2)), "h"))
            continue
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+\*\*([^*]+)\*\*", line)
        if article and m:
            seq.append((_norm_h(m.group(1)), "lead"))
    pos, converted = -1, {}
    for w, kind, par in want:
        nw = _norm_h(w)
        j = next((k for k in range(pos + 1, len(seq)) if seq[k][0] == nw and (seq[k][1] == "h" or kind == "h3")), None)
        if j is None:
            issues.append(f"Немає заголовка (або порушено порядок): «{w}»")
            continue
        pos = j
        if kind == "h3":
            converted.setdefault(par, []).append(seq[j][1] == "lead")
    conv = [p for p, v in converted.items() if v and all(v)]
    for p, v in converted.items():
        if any(v) and not all(v):
            issues.append(f"Блок «{p}»: частина H3 — підзаголовки, частина — пункти списку; змішувати не можна")
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
            (issues if kind == "ключ" and not text_types.is_article(text_types.resolve(project)) else warns).append(f"{kind} «{k}»: {total} входжень, норма {lo}–{hi} — додати {lo - total}")
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
    fi, fw, used = fact_checks(md, tz, lang, facts, project)
    si, sw = structure_checks(md, tz, lang, ttype)
    ai, aw = text_types.article_checks(md, ttype, [k for k, _ in tz.get(f"kw_{lang}", [])], lang)
    si += ai
    sw += aw
    issues += si + fi
    warns += sw + fw
    if facts and style in styles.STYLES:
        from .facts import STYLE_FACTS
        lo = min(STYLE_FACTS[style][0], len(facts))
        if len(used) < lo:
            warns.append(f"Використано фактів із джерелами: {len(used)}, стиль передбачає щонайменше {lo}")
    ti, tw, fstats = text_types.format_checks(md, text_types.resolve(project))
    issues += ti
    warns += tw
    styled = style in styles.STYLES
    (issues if styled else warns).extend(styles.ai_markers(plain))
    if styled:
        ls = styles.long_sentences(plain)
        if ls > 3:
            warns.append(f"Речень понад 25 слів: {ls} — за правилами стилю краще поділити")
    placeholders = [m.group(0) for m in PLACEHOLDER_RE.finditer(md)]
    return {"chars": n, "issues": list(dict.fromkeys(issues)), "warnings": list(dict.fromkeys(warns)), "keywords": kw_rep,
            "sections": sections, "placeholders": placeholders, "facts_used": used, "format": fstats, "converted": conv, "ok": not issues}


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
Збережи заголовки розділу й підрозділів дослівно, зміст, факти, посилання [джерело](URL), кнопку CTA, таблиці/списки й ключові слова з переліку. Скорочуй «воду» і повтори, а не суть; якщо треба додати — додай конкретику. Збережи стиль і тональність оригіналу. Мова — природна {lang_name}, без помилок. Поверни лише markdown цього розділу."""


async def fix_sections(md, tz, lang, brief_kw, log, style="default"):
    """Точкове виправлення обсягу: переписуємо найбільш відхилені розділи з чіткою ціллю."""
    import asyncio
    rep = check(md, tz, lang, style)
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
    for dev, title, act, lo, hi in cand[:3]:
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
            f"Ціль: {lo}–{hi} зн. (зараз {act}).\nКлючові слова, які треба зберегти, якщо вони є в розділі: {brief_kw}\n\n## Розділ\n{part}", 6000,
            step="тексти: підгонка розділів"))
        if headings(new) and _norm_h(headings(new)[0][1]) == _norm_h(title):
            md = md[:span[0]] + new.strip() + "\n\n" + md[span[1]:].lstrip()
            need -= abs(act - len(strip_md(new)))
    return md


AUDIT_TOOL = {
    "name": "submit_audit",
    "description": "Порушення ТЗ і помилки в тексті",
    "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "section": {"type": "string", "description": "заголовок розділу"},
        "problem": {"type": "string", "description": "що саме не так (з посиланням на вимогу ТЗ, якщо це порушення ТЗ)"},
        "quote": {"type": "string", "description": "фрагмент тексту, якого стосується (або порожньо, якщо чогось бракує)"},
        "fix": {"type": "string", "description": "як виправити конкретно"},
        "severity": {"type": "string", "enum": ["critical", "minor"]}},
        "required": ["section", "problem", "fix", "severity"]}}}, "required": ["items"]},
}
AUDIT_SYS = """Ти — прискіпливий шеф-редактор. Перевір, чи текст написано СТРОГО за ТЗ і чи готовий він до публікації без ручних правок.
Перевір:
1. КОЖЕН коментар ТЗ до кожного розділу: чи виконано вимогу (зміст, що згадати, кількість пунктів/етапів, формат — таблиця, список, етапи, формула з прикладом, акценти, CTA-текст і кнопка).
2. Смисл і логіка: суперечності, хибні твердження, нелогічні переходи, повтори між розділами, відповіді FAQ, що не відповідають на питання.
3. Факти: усе про компанію — лише з «Про компанію» (вигадані роки, клієнти, гарантії, терміни, ціни, сертифікати — critical); статистика — лише з «Перевірених фактів», цифра/рік/країна збігаються з цитатою, є посилання.
4. Готовність: жодних плейсхолдерів, «[уточнити]», недописаних речень, звернень до копірайтера.
5. Мова: орфографія, граматика, пунктуація, суржик/кальки, канцелярит; стиль і тональність — за розділом «Стиль і тональність» (якщо є).
6. Формат — за розділом «Формат тексту»: для статті «рубаний» текст (дрібні абзаци по 1–2 речення, пояснення списками, пункти-фрагменти) — critical із зазначенням розділів.
7. Для статей — «Правила редакції» і «Формат тексту» з брифу: риторичні питання, «Так/Ні» на початку, жирний в абзацах, абзаци-переліки й констатації без висновку, цифри не по темі тези чи одне джерело на кілька тез, пороги сервісів як норми Google, інтерпретація як позиція платформи, вигадані приклади без позначки «Умовний приклад», повтори між розділами, FAQ, що переказує тіло; для медіа — рамка медіа.
НЕ перевіряй загальний обсяг і кількість входжень ключів — це робить автоматика.
Повертай ЛИШЕ реальні порушення, кожне — з конкретним фрагментом і способом виправлення. Не вимагай того, чого немає в ТЗ, і не пропонуй смакових правок. critical — порушення ТЗ, фактів, смислу, мовні помилки; minor — дрібні стилістичні покращення. Якщо все добре — порожній список."""


async def audit(md: str, brief: str) -> list[dict]:
    import asyncio
    if config.MOCK or not config.TEXT_AUDIT:
        return []
    try:
        d = await asyncio.to_thread(llm.call_tool, AUDIT_SYS, [(brief, True), f"## Текст для перевірки\n{md}"], AUDIT_TOOL, 6000,
                                    lambda x: "items" in x, config.ANTHROPIC_MODEL_LIGHT, "тексти: аудит ТЗ")
        return [i for i in d.get("items", []) if i.get("problem")]
    except Exception:  # noqa: BLE001
        return []


def _score(rep, spell, aud=None):
    return len(rep["issues"]) * 2 + 0.5 * len(spell)


def _report(rep, spell, aud):
    sec = [f"- «{t}»: {a} зн. (ціль {lo}–{hi})" for t, a, lo, hi in rep["sections"] if a and lo]
    crit = [a for a in aud if a.get("severity") == "critical"]
    minor = [a for a in aud if a.get("severity") != "critical"]
    fmt = lambda a: f"- [{a.get('section', '')}] {a['problem']}" + (f" — «{a['quote'][:200]}»" if a.get("quote") else "") + f" → {a.get('fix', '')}"  # noqa: E731
    return "\n".join(["## Звіт автоматичної перевірки (виправити все)", *([f"- {x}" for x in rep["issues"]] or ["- проблем немає"]),
                      *(["\n## Аудит відповідності ТЗ — критичне (виправити все)"] + [fmt(a) for a in crit] if crit else []),
                      *(["\n## Аудит — бажані покращення (виправ, якщо це не порушує ТЗ)"] + [fmt(a) for a in minor] if minor else []),
                      *(["\n## Обсяги розділів"] + sec if sec else []),
                      *(["\n## Зауваження LanguageTool (перевір, виправ справжні помилки)"] + [f"- {x}" for x in spell] if spell else []),
                      "\n## Фактичні входження ключів/LSI", *[f"- {k[0]} «{k[1]}»: {k[2]} (норма {k[3]})" for k in rep["keywords"]],
                      "\nВажливо: заголовки не змінюй; кількість входжень зменшуй синонімами, займенниками й перебудовою речень; "
                      "обсяг розділів тримай у цілях; змінюй лише проблемні фрагменти."])


META_TOOL = {
    "name": "submit_meta",
    "description": "Title, Description і теги статті",
    "input_schema": {"type": "object", "properties": {
        "title": {"type": "string", "description": "до 60–65 знаків, з головним ключем, інформаційний"},
        "description": {"type": "string", "description": "до 155–160 знаків, про що стаття і яку користь дає"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "3–6 тегів"}},
        "required": ["title", "description", "tags"]},
}
META_SYS = """Ти — редактор медіа MoreThanMedia. Склади Title, Description і теги для статті {lang_name}.
Без бренду Elit-Web та будь-яких агенцій, без комерційних слів («послуги», «замовити», «консультація», «ціна», «безкоштовно»), без закликів. Інформаційно, з головним ключем у природній формі, без емодзі й CAPS."""


async def media_meta(md: str, tz: dict, lang: str) -> dict:
    import asyncio
    h1 = next((h for lvl, h in headings(md) if lvl == 1), tz.get(f"name_{lang}") or "")
    if config.MOCK:
        return {"title": h1[:65], "description": strip_md(md)[:155], "tags": [k for k, _ in tz.get(f"kw_{lang}", [])][:4]}
    kw = ", ".join(k for k, _ in tz.get(f"kw_{lang}", [])[:6])
    try:
        d = await asyncio.to_thread(llm.call_tool, META_SYS.replace("{lang_name}", LANG_NAME[lang]),
                                    f"Ключі: {kw}\n\nСтаття:\n{md[:12000]}", META_TOOL, 1500, lambda x: bool(x.get("title")),
                                    config.ANTHROPIC_MODEL_LIGHT, "тексти: Title/Description")
        return {"title": d["title"], "description": d.get("description", ""), "tags": d.get("tags", [])}
    except Exception:  # noqa: BLE001
        return {"title": h1[:65], "description": "", "tags": []}


async def write_text(tz: dict, lang: str, project: dict, research: dict | None, log, style: str = "default",
                     facts: list | None = None) -> dict:
    import asyncio
    style = styles.resolve(style, tz)
    ttype = text_types.resolve(project)
    tz = text_types.adapt_tz(tz, ttype)
    if tz.get("_dropped"):
        log(f"{lang.upper()}: для медіа прибрано з ТЗ: " + "; ".join(tz["_dropped"]))
    log(f"{lang.upper()}: тип — {text_types.TYPES[ttype]}; стиль — {styles.name(style)}")
    brief = tz_brief(tz, lang, project, research, style, facts)
    kwl = ", ".join(k for k, _ in tz.get(f"kw_{lang}", []))
    ck = lambda t: check(t, tz, lang, style, facts, project)  # noqa: E731
    system = writer_system(lang, ttype)
    if config.MOCK:
        md = _mock_text(tz, lang, facts)
    else:
        log(f"{lang.upper()}: Claude пише текст")
        md = _clean_md(await asyncio.to_thread(
            llm.call_text, system, [(brief, True), "Напиши текст сторінки."], 32000, step="тексти: автор"))

    async def edit(report: str, label: str):
        try:
            ans = await asyncio.to_thread(llm.call_text, system, [(brief, True), f"{EDITOR_SYS}\n\n{report}\n\n## Поточний текст\n{md}"],
                                          24000, step=label, min_len=1, on_truncate="partial")
        except Exception as e:  # noqa: BLE001  редактор не має валити весь текст
            log(f"{lang.upper()}: редактор не відповів ({e}) — лишаю попередню версію")
            return md, 0
        new, ok, bad = apply_patches(md, ans)
        log(f"{lang.upper()}: правок застосовано {ok}" + (f", не знайдено у тексті {bad}" if bad else ""))
        return new, ok

    versions, rounds, aud = [], 0, []
    while True:
        if any(x.startswith("Загальний обсяг") for x in ck(md)["issues"]) and not config.MOCK:
            md = await fix_sections(md, tz, lang, kwl, log, style)
        rep, spell = ck(md), await languagetool(md, lang)
        cur_aud = await audit(md, brief) if rounds == 0 else []  # аудит Claude — на чернетці; далі — автоматика
        aud = aud or cur_aud
        crit = sum(1 for a in cur_aud if a.get("severity") == "critical")
        log(f"{lang.upper()}: перевірка (раунд {rounds}) — {len(rep['issues'])} проблем автоматики"
            + (f", {crit} критичних зауважень аудиту ТЗ" if rounds == 0 else "") + f", {len(spell)} LanguageTool, {rep['chars']} зн.")
        versions.append((_score(rep, spell), -rounds, md, rep, spell))
        if (rep["ok"] and not crit and len(spell) < 2) or rounds >= config.TEXT_MAX_FIX_ROUNDS or config.MOCK:
            break
        rounds += 1
        log(f"{lang.upper()}: редактор виправляє (раунд {rounds})")
        md, ok = await edit(_report(rep, spell, cur_aud), "тексти: редактор")
        if not ok:
            break
    best = min(versions, key=lambda v: (v[0], v[1]))
    md, rep, spell = best[2], best[3], best[4]
    if len(versions) > 1 and best is not versions[-1]:
        log(f"{lang.upper()}: остання редакція не краща — беру найкращу версію (раунд {-best[1]})")
    if not config.MOCK:
        if rounds == 0:
            # коректура правками; приймаємо лише без погіршень
            log(f"{lang.upper()}: фінальна коректура")
            fixed, ok = await edit("## Звіт перевірки\n- Автоматичних проблем не знайдено. Зроби лише коректуру: орфографія, пунктуація, "
                                   "граматика, стиль, логіка. Нічого не скорочуй і не додавай.", "тексти: коректура")
            r2 = ck(fixed)
            if ok and len(r2["issues"]) <= len(rep["issues"]):
                md, rep = fixed, r2
        else:
            aud = await audit(md, brief)  # фінальний аудит — для звіту
    leftover = [a for a in aud if a.get("severity") == "critical"]
    rep = {**rep, "audit": aud}
    if leftover:
        rep["issues"] = rep["issues"] + [f"Аудит ТЗ: [{a.get('section', '')}] {a['problem']}" for a in leftover]
        rep["ok"] = False
    meta = await media_meta(md, tz, lang) if ttype == text_types.MEDIA else None
    rep["dropped"] = tz.get("_dropped", [])
    return {"lang": lang, "markdown": md, "report": rep, "spelling": spell, "rounds": rounds, "facts": facts or [], "meta": meta,
            "style": style, "style_name": styles.name(style), "text_type": ttype, "text_type_name": text_types.TYPES[ttype]}


def _mock_text(tz, lang, facts=None):
    L = 0 if lang == "ru" else 1
    lines = []
    kw = (tz.get(f"kw_{lang}") or [("", "")])[0][0]
    for b in tz["blocks"]:
        t = b[0]
        if t == "h1":
            src = ""
            if facts:
                f = facts[0]
                src = f" За даними [{f['org']}]({f['url']}), {f['year']}, {f['number']} відвідувань обриваються."
            lines.append(f"# {b[1 + L]}\n\nТестовий абзац про {kw}.{src}")
        elif t in ("h2", "h3"):
            lines.append(f"{'##' if t == 'h2' else '###'} {b[1 + L]}\n\nТестовий текст розділу.")
        elif t in ("cta", "faq"):
            lines.append(f"## {t.upper()}\n\nТекст.")
        elif t == "q":
            lines.append(f"### {b[1 + L]}\n\n" + "Відповідь на питання. " * 15)
    return "\n\n".join(lines)
