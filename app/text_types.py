"""Тип тексту: SEO-текст для посадкової сторінки, стаття для блогу компанії, стаття для медіа.

Тип впливає на ТЗ (шаблон і структура), на формат написання (довжина абзаців, кількість списків)
і на автоматичну перевірку формату.
"""
import re

LANDING, ARTICLE, MEDIA = "landing", "article", "media"

TYPES = {
    LANDING: "SEO-текст для посадкової сторінки",
    ARTICLE: "Стаття (блог компанії)",
    MEDIA: "Стаття для медіа (MoreThanMedia)",
}


def resolve(project: dict | None) -> str:
    project = project or {}
    t = project.get("text_type")
    if t in TYPES:
        return t
    return MEDIA if project.get("media") else LANDING


def valid(t, media: bool = False) -> str:
    return t if t in TYPES else (MEDIA if media else LANDING)


def is_article(t: str) -> bool:
    return t in (ARTICLE, MEDIA)


def catalog() -> list[dict]:
    return [{"id": k, "name": v} for k, v in TYPES.items()]


# ---------------------------------------------------------------- формат написання
LANDING_FORMAT = """## Формат тексту: SEO-текст для посадкової сторінки
Абзаци короткі й ємні (2–4 речення), розділи легко сканувати: списки, таблиці й етапи — там, де їх просить ТЗ або де є перелік однорідних пунктів. Від імені компанії («ми»), звернення на «ви»."""

ARTICLE_FORMAT = """## Формат тексту: СТАТТЯ (має пріоритет над будь-якими правилами про короткі абзаци й списки — у стилі, еталоні та загальних правилах)
Це не посадкова сторінка, а стаття, яку читають від початку до кінця. Текст має текти, а не бути нарізаним на пункти.
- Основа — розгорнуті абзаци по 3–6 речень (приблизно 50–110 слів). Абзац розвиває думку: теза → пояснення → приклад або наслідок для читача. Між абзацами — логічні переходи.
- Абзаців з одного речення — не більше 1–2 на весь текст, і лише для акценту.
- Списки — тільки там, де справді є послідовні кроки, критерії вибору або перелік однорідних речей, або де ТЗ прямо вимагає список. Не більше одного списку на два розділи H2. Пункт списку — повне речення (або 1–3 речення з поясненням), а не фрагмент у 2–4 слова. Пояснення, аргументи й причини пиши абзацами, не списком.
- Таблиця — лише якщо її вимагає ТЗ або є порівняння кількох варіантів за кількома параметрами.
- **Жирний** — точково (терміни, ключова думка), не в кожному абзаці; не роби «жирних підзаголовків» замість абзаців.
- Вступ — 1–2 абзаци, що вводять у тему через ситуацію або питання читача, без продажу. Завершення — абзац із практичним підсумком (що робити читачеві), без штампів.
- З еталонного тексту бери лише рівень якості та грамотності — НЕ його форматування (еталон — посадкова сторінка)."""

ARTICLE_VOICE = "Голос — експерт компанії, що ділиться знанням («ми» доречне, але без продажу в кожному розділі). CTA — один ненав'язливий абзац наприкінці (за коментарем ТЗ), без тиску."


def format_block(t: str) -> str:
    if t == LANDING:
        return LANDING_FORMAT
    return ARTICLE_FORMAT + ("\n- " + ARTICLE_VOICE if t == ARTICLE else "")


# ---------------------------------------------------------------- ТЗ
TZ_ARTICLE = """## ТИП ТЕКСТУ: СТАТТЯ{media} — це важливіше за шаблон, обраний за інтентом
- template = «Експертна стаття / лонгрід» (навіть якщо у видачі посадкові сторінки — тоді в intent коротко зазнач ризик невідповідності інтенту).
- Структура статті: вступ → розділи, що відповідають на питання читача (H2 — питання або чіткі твердження) → практичні розділи (як зробити, як обрати, типові помилки, приклад) → підсумок → FAQ. Без блоків «Ціни», «Етапи роботи з нами», «Чому обирають нас», «Кейси», «Що входить у послугу».
- CTA — один, м'який, наприкінці{media_cta}.
- У коментарях до блоків вимагай розгорнутих абзаців по 3–6 речень. Списки й таблиці — лише там, де справді є кроки, критерії чи порівняння, не більше одного на два H2; прямо пиши «абзацами, без списку» для пояснювальних блоків.
- Обсяг — за медіаною статей у ТОПі (якщо їх немає — 6 000–10 000 зн.)."""


def tz_block(t: str) -> str:
    if not is_article(t):
        return ""
    media = t == MEDIA
    return TZ_ARTICLE.format(
        media=" ДЛЯ МЕДІА (без згадок агенції, нейтрально)" if media else " ДЛЯ БЛОГУ КОМПАНІЇ",
        media_cta=": наступний крок для читача, без заклику замовити послуги" if media else ": ненав'язливе запрошення звернутися до компанії")


# ---------------------------------------------------------------- перевірка формату
LIST_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _words(s: str) -> int:
    return len(re.findall(r"[\w’'ʼ-]+", s))


def format_checks(md: str, t: str):
    """(issues, warnings, stats) — лише для статей: довжина абзаців і частка списків."""
    if not is_article(t):
        return [], [], {}
    body = re.split(r"^##\s+FAQ\s*$", md, flags=re.M | re.I)[0]
    body = re.sub(r"^##\s+CTA\s*$.*?(?=^##\s|\Z)", "", body, flags=re.M | re.I | re.S)
    paras, lists, list_items = [], [], []
    cur_list = []
    for block in re.split(r"\n\s*\n", body):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        if lines[0].lstrip().startswith("#") and len(lines) == 1:
            continue
        lines = [l for l in lines if not l.lstrip().startswith("#")]
        if not lines:
            continue
        if all(l.lstrip().startswith("|") for l in lines):
            continue
        items = [l for l in lines if LIST_RE.match(l)]
        if items and len(items) >= len(lines) - 1:
            cur_list += items
            continue
        if cur_list:
            lists.append(cur_list)
            list_items += cur_list
            cur_list = []
        text = " ".join(lines)
        if re.fullmatch(r"\*\*\[[^\]]+\]\*\*", text.strip()):
            continue
        paras.append(text)
    if cur_list:
        lists.append(cur_list)
        list_items += cur_list
    issues, warns = [], []
    pw = [_words(p) for p in paras]
    lw = sum(_words(LIST_RE.sub("", x)) for x in list_items)
    total = sum(pw) + lw
    stats = {"paragraphs": len(paras), "avg_words": round(sum(pw) / len(pw)) if pw else 0, "lists": len(lists),
             "list_share": round(100 * lw / total) if total else 0}
    sents = [max(1, len(re.findall(r"[.!?…](?:\s|$)", p))) for p in paras]
    stats["avg_sentences"] = round(sum(sents) / len(sents), 1) if sents else 0
    if pw and (stats["avg_sentences"] < 2.5 or stats["avg_words"] < 35):
        issues.append(f"Формат статті: абзаци закороткі — у середньому {stats['avg_sentences']} речення / {stats['avg_words']} слів, "
                      "треба 3–6 речень (≈ 50–110 слів). Об'єднай дрібні абзаци, розгорни думки")
    short = sum(1 for p in paras if len(re.findall(r"[.!?…](?:\s|$)", p)) <= 1 and _words(p) < 25)
    if short > 2:
        issues.append(f"Формат статті: {short} абзаців з одного короткого речення — допустимо 1–2")
    if total and stats["list_share"] > 25:
        issues.append(f"Формат статті: забагато списків — {stats['list_share']}% тексту (допустимо до 25%). Перепиши пояснювальні списки абзацами")
    h2 = len(re.findall(r"^##\s+(?!FAQ|CTA)", md, flags=re.M | re.I))
    if lists and len(lists) > max(1, (h2 + 1) // 2):
        warns.append(f"Формат статті: {len(lists)} списків на {h2} розділів H2 — краще не більше одного на два розділи")
    if list_items:
        avg_item = lw / len(list_items)
        if avg_item < 7:
            issues.append(f"Формат статті: пункти списків — фрагменти по {avg_item:.0f} слів; пункт має бути повним реченням з поясненням")
    return issues, warns, stats
