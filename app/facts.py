"""Пошук і перевірка фактів із джерелами для SEO-текстів (Serper.dev → сторінки джерел → Claude витягає → код перевіряє).

Факт потрапляє в текст, лише якщо:
- сторінка з білого списку першоджерел (платформи, дослідницькі компанії, держоргани);
- цитата й цифра ДОСЛІВНО є в тексті сторінки (перевіряє код, не модель);
- рік даних не старший за FACTS_MAX_AGE років і цей рік є на сторінці (або це рік публікації).
"""
import asyncio
import datetime as dt
import difflib
import io
import re

import httpx

from . import config, serp
from .scraper import UA, domain

# Пріоритет джерел (документ «Правила роботи зі статистикою»): 1 — офіційні дані платформ і держоргани, 2 — дослідження
TIER1 = [
    "google.com", "blog.google", "thinkwithgoogle.com", "web.dev", "developers.google.com", "support.google.com",
    "ads.google.com", "business.google.com", "searchcentral.google.com", "about.google",
    "meta.com", "about.fb.com", "facebook.com/business", "business.instagram.com", "about.instagram.com",
    "newsroom.tiktok.com", "ads.tiktok.com", "tiktok.com/business", "openai.com", "perplexity.ai", "anthropic.com",
    "microsoft.com", "bing.com", "apple.com", "business.linkedin.com", "news.linkedin.com", "economicgraph.linkedin.com",
    "blog.youtube", "youtube.com/creators", "blog.hubspot.com",
    "amazon.com", "aboutamazon.com", "sell.amazon.com", "etsy.com", "shopify.com", "rozetka.com.ua", "prom.ua",
    "epicentrk.ua", "telegram.org", "viber.com", "w3.org", "httparchive.org", "almanac.httparchive.org", "chromium.org",
    "gov.ua", "gov", "europa.eu", "oecd.org", "worldbank.org", "un.org", "itu.int", "imf.org", "bank.gov.ua",
    "ukrstat.gov.ua", "stat.gov.ua", "diia.gov.ua", "thedigital.gov.ua",
]
TIER2 = [
    "statista.com", "gartner.com", "similarweb.com", "kantar.com", "kantar.ua", "gradus.app", "gradus.app",
    "gfk.com", "nielsen.com", "ipsos.com", "forrester.com", "emarketer.com", "insiderintelligence.com",
    "mckinsey.com", "bcg.com", "deloitte.com", "pwc.com", "ey.com", "kpmg.com", "accenture.com", "bain.com",
    "pewresearch.org", "datareportal.com", "wearesocial.com", "meltwater.com", "hootsuite.com",
    "gs.statcounter.com", "statcounter.com", "w3techs.com", "builtwith.com", "semrush.com", "ahrefs.com", "moz.com",
    "hubspot.com", "salesforce.com", "mailchimp.com", "litmus.com", "klaviyo.com", "campaignmonitor.com",
    "brevo.com", "omnisend.com", "baymard.com", "nngroup.com", "edelman.com", "brightlocal.com", "sparktoro.com",
    "ain.ua", "dou.ua", "kiis.com.ua", "razumkov.org.ua", "rating.ua", "ratinggroup.ua", "iab.com", "iabeurope.eu",
    "iab.org.ua", "uaca.org.ua", "eba.com.ua", "opendatabot.ua", "youcontrol.com.ua", "cbre.com", "idc.com",
    "canalys.com", "counterpointresearch.com", "comscore.com", "sensortower.com", "data.ai", "appsflyer.com",
    "adjust.com", "wordstream.com", "reuters.com", "reutersinstitute.politics.ox.ac.uk", "ftc.gov",
]
STYLE_FACTS = {"A": (3, 6), "B": (2, 4), "V": (2, 3), "G": (2, 4), "default": (1, 3)}

NUM_RE = re.compile(r"\d")


def _extra():
    return [d.strip().lower() for d in (config.FACTS_EXTRA_DOMAINS or "").split(",") if d.strip()]


# користувацький контент на доменах платформ — не першоджерело
EXCLUDE = ["sites.google.com", "docs.google.com", "drive.google.com", "groups.google.com", "play.google.com",
           "books.google.com", "translate.google.com", "/thread/", "/community/", "/forum", "/user/"]


def tier(url: str) -> int:
    d = domain(url)
    path = d + re.sub(r"^https?://[^/]+", "", url.lower())
    if any((x in path) if "/" in x else (d == x or d.endswith("." + x)) for x in EXCLUDE):
        return 0

    rest = path[len(d):]

    def match(lst):
        for x in lst:
            xd, _, xp = x.partition("/")
            if (d == xd or d.endswith("." + xd)) and (not xp or rest.startswith("/" + xp)):
                return True
        return False

    if match(TIER1):
        return 2
    if match(TIER2) or match(_extra()):
        return 1
    return 0


def norm(s: str) -> str:
    s = (s or "").lower().replace(" ", " ").replace(" ", " ").replace(" ", " ")
    s = re.sub(r"[«»“”„\"'`’ʼ]", "", s)
    s = re.sub(r"[‐‑‒–—−]", "-", s)
    s = re.sub(r"(\d)[ ,](\d{3})\b", r"\1\2", s)  # 1 200 / 1,200 → 1200
    return re.sub(r"\s+", " ", s).strip()


def digits_of(num: str) -> str:
    n = norm(num)
    m = re.search(r"\d+(?:[.,]\d+)?", n)
    return m.group(0).replace(",", ".") if m else ""


def quote_in_page(quote: str, page: str) -> bool:
    q, p = norm(quote), norm(page)
    if len(q) < 25:
        return False
    if q in p:
        return True
    # PDF / переноси рядків: шукаємо вікно навколо першого числа цитати
    nm = re.search(r"\d+(?:[.,]\d+)?", q)
    if not nm:
        return False
    for m in re.finditer(re.escape(nm.group(0)), p):
        a = max(0, m.start() - nm.start() - 40)
        win = p[a:a + len(q) + 80]
        sm = difflib.SequenceMatcher(None, q, win, autojunk=False)
        if sum(b.size for b in sm.get_matching_blocks()) >= 0.9 * len(q):
            wn = re.findall(r"\d+", win)
            if all(wn.count(x) >= re.findall(r"\d+", q).count(x) for x in set(re.findall(r"\d+", q))):
                return True
    return False


# ---------------------------------------------------------------- Claude: запити та витяг
Q_TOOL = {
    "name": "submit_queries",
    "description": "Пошукові запити для пошуку статистики з першоджерел",
    "input_schema": {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "object", "properties": {
        "q": {"type": "string"}, "lang": {"type": "string", "enum": ["uk", "ru", "en"]}}, "required": ["q", "lang"]}}},
        "required": ["queries"]},
}
Q_SYS = """Ти — фактчекер редакції. Під тему сторінки підбери 6–7 пошукових запитів Google, щоб знайти СВІЖУ статистику (дані за {y1}–{y0} роки) з першоджерел, яка підсилить текст: попит, поведінка аудиторії, ефективність каналу, вимоги платформ, ринкові частки.
Пріоритет джерел: офіційні дані/довідки/блоги платформ (Google, Meta, TikTok, OpenAI, Amazon, Rozetka, Prom…), дослідницькі компанії з відкритою методологією (Statista, Gartner, Similarweb, Kantar, Gradus, GfK, DataReportal, McKinsey, HubSpot Research…), держоргани.
- 2–3 запити українською/російською — про український ринок (Gradus, Kantar Україна, ain.ua/dou.ua дослідження, Держстат, НБУ);
- 3–4 запити англійською — глобальні дані платформ і досліджень; додавай рік ({y0} або {y1}) і слова statistics / report / study / benchmark;
- якщо знаєш конкретний звіт на цю тему (назва + організація), дай запит, що знайде саме його сторінку (можна site:домен).
Не шукай конкурентів-агенції та їхні блоги."""

E_TOOL = {
    "name": "submit_facts",
    "description": "Факти з цифрами, дослівно знайдені на сторінках",
    "input_schema": {"type": "object", "properties": {"facts": {"type": "array", "items": {"type": "object", "properties": {
        "url": {"type": "string"},
        "quote": {"type": "string", "description": "ДОСЛІВНЕ речення зі сторінки (мовою оригіналу), що містить цифру"},
        "number": {"type": "string", "description": "цифра так, як у цитаті (напр. 53%, 1,2 млн)"},
        "org": {"type": "string", "description": "організація — автор даних (не сайт-переказувач)"},
        "year": {"type": "integer", "description": "рік даних або публікації"},
        "geo": {"type": "string", "description": "країна/регіон вибірки: Україна, США, світ, Європа…"},
        "claim_ua": {"type": "string", "description": "переказ українською з контекстом (хто, де, коли, кого опитали), без перебільшень"},
        "claim_ru": {"type": "string", "description": "той самий переказ російською"},
        "fit": {"type": "integer", "description": "1–5, наскільки факт доречний для цієї сторінки"}},
        "required": ["url", "quote", "number", "org", "year", "geo", "claim_ua", "claim_ru", "fit"]}}},
        "required": ["facts"]},
}
E_SYS = """Ти — фактчекер. З фрагментів сторінок випиши факти з цифрами, корисні для тексту на тему сторінки.
Суворі правила:
- quote — ДОСЛІВНЕ речення з фрагмента, символ у символ (його перевірить програма; переказ = факт відкинуто).
- Лише дані, які сторінка подає як свої або з названим першоджерелом; org — організація, що провела дослідження / володіє даними.
- year — рік даних (або рік публікації, якщо він на сторінці). Дані старші за {y1} рік не бери.
- claim_* передає цифру точно, з тим самим контекстом (країна, вибірка, період), без узагальнень; якщо дані не по Україні — так і напиши («у США», «у світі»).
- Не бери рекламні обіцянки, ціни сервісів, прогнози без методології, цифри без зрозумілого змісту.
- До 4 фактів зі сторінки, лише найкорисніші для теми (fit ≥ 3)."""


def _relevant(text: str, limit: int = 6000) -> str:
    """Фрагменти з цифрами (щоб не віддавати Claude всю сторінку)."""
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    out, n = [], 0
    for p in paras:
        if len(p) < 40 or not NUM_RE.search(p) or not re.search(r"\d\s?%|\d{2,}|млн|млрд|million|billion|percent|відсот|процент", p, re.I):
            continue
        p = p[:700]
        out.append(p)
        n += len(p)
        if n > limit:
            break
    return "\n".join(out)


# ---------------------------------------------------------------- завантаження сторінок (HTML і PDF)
async def _fetch(url: str, cl: httpx.AsyncClient) -> dict | None:
    try:
        r = await cl.get(url, headers={"User-Agent": UA, "Accept-Language": "uk,ru;q=0.8,en;q=0.7"})
        if r.status_code >= 400:
            return None
        ct = r.headers.get("content-type", "")
        if "pdf" in ct or url.lower().split("?")[0].endswith(".pdf"):
            return await asyncio.to_thread(_pdf, url, r.content)
        if "html" not in ct and ct:
            return None
        return await asyncio.to_thread(_html, url, r.text[:3_000_000])
    except Exception:  # noqa: BLE001
        return None


def _html(url, html):
    import trafilatura
    text = trafilatura.extract(html, include_tables=True, favor_recall=True) or ""
    date = ""
    try:
        meta = trafilatura.extract_metadata(html)
        date = (meta.date or "") if meta else ""
    except Exception:  # noqa: BLE001
        pass
    return {"url": url, "text": text, "date": date} if len(text) > 300 else None


def _pdf(url, data):
    from pypdf import PdfReader
    rd = PdfReader(io.BytesIO(data))
    text = "\n".join((p.extract_text() or "") for p in rd.pages[:40])
    return {"url": url, "text": text, "date": ""} if len(text) > 300 else None


# ---------------------------------------------------------------- перевірка
def verify(f: dict, pages: dict, today: dt.date | None = None) -> str:
    """'' — факт пройшов; інакше причина відмови."""
    today = today or dt.date.today()
    pg = pages.get(f.get("url", ""))
    if not pg:
        return "URL не з завантажених сторінок"
    if tier(f["url"]) == 0:
        return "домен не з переліку першоджерел"
    if not quote_in_page(f.get("quote", ""), pg["text"]):
        return "цитати немає на сторінці"
    d = digits_of(f.get("number", ""))
    if not d or d not in norm(f["quote"]).replace(",", "."):
        return "цифри немає в цитаті"
    y = int(f.get("year") or 0)
    if y < today.year - config.FACTS_MAX_AGE or y > today.year:
        return f"дані за {y} рік — застарілі"
    if str(y) not in pg["text"] and not pg.get("date", "").startswith(str(y)):
        return "рік даних не підтверджено сторінкою"
    if int(f.get("fit") or 0) < 3:
        return "нерелевантний"
    return ""


def _mock(tz):
    y = dt.date.today().year
    return [{"id": "F1", "url": "https://www.thinkwithgoogle.com/mock-study", "domain": "thinkwithgoogle.com", "org": "Google",
             "year": y, "geo": "світ", "number": "53%",
             "quote": f"In {y}, 53% of mobile site visits were abandoned if a page took longer than three seconds to load.",
             "claim_ua": f"За даними Google ({y}), 53% відвідувань мобільних сайтів у світі обриваються, якщо сторінка вантажиться довше 3 секунд.",
             "claim_ru": f"По данным Google ({y}), 53% посещений мобильных сайтов в мире прерываются, если страница грузится дольше 3 секунд.",
             "tier": 2}]


async def research(tz: dict, style: str, project: dict, log) -> list[dict]:
    """Перевірені факти для сторінки (одні на обидві мови)."""
    if config.MOCK:
        return _mock(tz)
    if not config.FACTS_ENABLED or not config.SERPER_API_KEY or not config.ANTHROPIC_API_KEY:
        return []
    from . import llm
    today = dt.date.today()
    y0, y1 = today.year, today.year - config.FACTS_MAX_AGE
    heads = [b[2] or b[1] for b in tz.get("blocks", []) if b[0] in ("h1", "h2")]
    topic = (f"Сторінка: {tz.get('name_ua') or tz.get('name_ru')}\nРозділи: " + " | ".join(heads[:14])
             + f"\nКлючі: {', '.join(k for k, _ in (tz.get('kw_ua') or tz.get('kw_ru') or [])[:8])}")
    log("факти: Claude підбирає пошукові запити")
    qd = await asyncio.to_thread(llm.call_tool, Q_SYS.format(y0=y0, y1=y1), topic, Q_TOOL, 2000,
                                 lambda d: bool(d.get("queries")))
    queries = qd["queries"][:7]
    results = await asyncio.gather(*(serp.search(q["q"], {"uk": "ua", "ru": "ru", "en": "en"}.get(q.get("lang"), "ua"), 10)
                                     for q in queries), return_exceptions=True)
    cand = {}
    for res in results:
        if isinstance(res, Exception):
            continue
        for o in res["organic"]:
            u = o["url"]
            t = tier(u)
            if t and u not in cand:
                cand[u] = (t, o.get("position") or 10)
    per_dom, urls = {}, []
    for u, (t, pos) in sorted(cand.items(), key=lambda x: (-x[1][0], x[1][1])):
        d = domain(u)
        if per_dom.get(d, 0) >= 2:
            continue
        per_dom[d] = per_dom.get(d, 0) + 1
        urls.append(u)
        if len(urls) >= config.FACTS_MAX_PAGES:
            break
    log(f"факти: {len(queries)} запитів Serper, сторінок першоджерел: {len(urls)}")
    if not urls:
        return []
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as cl:
        fetched = await asyncio.gather(*(_fetch(u, cl) for u in urls))
    pages = {p["url"]: p for p in fetched if p}
    chunks = [f"### URL: {u}\nДата сторінки: {p.get('date') or 'невідома'}\n{_relevant(p['text'])}" for u, p in pages.items()]
    chunks = [c for c in chunks if c.count("\n") > 2]
    if not chunks:
        log("факти: на сторінках не знайшлося фрагментів із цифрами")
        return []
    ed = await asyncio.to_thread(llm.call_tool, E_SYS.format(y1=y1), topic + "\n\n" + "\n\n".join(chunks)[:60000], E_TOOL, 8000,
                                 lambda d: "facts" in d)
    ok, rejected, seen = [], {}, set()
    for f in ed.get("facts", []):
        f["url"] = f.get("url", "").strip()
        why = verify(f, pages, today)
        if why:
            rejected[why] = rejected.get(why, 0) + 1
            continue
        key = (digits_of(f["number"]), f["org"].lower())
        if key in seen:
            continue
        seen.add(key)
        f["domain"], f["tier"] = domain(f["url"]), tier(f["url"])
        ok.append(f)
    ok.sort(key=lambda f: (-f["tier"], -int(f.get("fit") or 0), -int(f.get("year") or 0)))
    ok = ok[:8]
    for i, f in enumerate(ok, 1):
        f["id"] = f"F{i}"
    log(f"факти: перевірено {len(ok)}" + (f"; відкинуто: " + ", ".join(f"{k} — {v}" for k, v in rejected.items()) if rejected else ""))
    return ok


def brief_block(facts: list[dict], lang: str, style: str) -> str:
    lo, hi = STYLE_FACTS.get(style, STYLE_FACTS["default"])
    if not facts:
        return ("## Перевірені факти\nПеревірених фактів зі свіжих першоджерел не знайдено. У тексті НЕ має бути статистики ринку/досліджень "
                "та відсотків з пам'яті — формулюй думки якісно, без чисел (дозволені лише числа з ТЗ, «Про компанію» та умовні розрахунки з позначкою «умовно»).")
    out = [f"## Перевірені факти (ЄДИНЕ джерело статистики; використай {lo}–{min(hi, len(facts))} доречних)",
           "Формат у тексті: «За даними [Організація](URL), рік, …» — назва джерела є посиланням на URL факту. Цифру й контекст (країна, вибірка, період) передавай точно; якщо дані не по Україні — так і пиши. Кожну цифру пов'язуй із висновком або дією для читача."]
    for f in facts:
        out.append(f"[{f['id']}] {f['claim_' + ('ru' if lang == 'ru' else 'ua')]}\n   джерело: {f['org']}, {f['year']}, {f['geo']} — {f['url']}\n   цитата: «{f['quote']}»")
    return "\n".join(out)
