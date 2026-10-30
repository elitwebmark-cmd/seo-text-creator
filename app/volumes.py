"""Частотність і підказки ключів: DataForSEO (Google Ads / Keyword Planner) + Serper Autocomplete."""
import re

import httpx

from . import config
from .serp import HL

DFS = "https://api.dataforseo.com/v3/keywords_data/google_ads"


def dfs_enabled() -> bool:
    return bool(config.DATAFORSEO_LOGIN and config.DATAFORSEO_PASSWORD) and not config.MOCK


def _clean(kws):
    out, seen = [], set()
    for k in kws:
        k = " ".join(str(k).lower().split())
        if not k or len(k) > 80 or len(k.split()) > 10 or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


async def _dfs(endpoint: str, payload: dict) -> list:
    async with httpx.AsyncClient(timeout=90, auth=(config.DATAFORSEO_LOGIN, config.DATAFORSEO_PASSWORD)) as cl:
        r = await cl.post(f"{DFS}/{endpoint}/live", json=[payload])
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"DataForSEO HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400 or data.get("status_code") not in (20000, None):
        raise RuntimeError(f"DataForSEO {data.get('status_code', r.status_code)}: {data.get('status_message', r.text[:200])}")
    task = (data.get("tasks") or [{}])[0]
    if task.get("status_code") not in (20000, None):
        raise RuntimeError(f"DataForSEO {task.get('status_code')}: {task.get('status_message')}")
    return task.get("result") or []


async def diagnose() -> dict:
    """Перевірка доступу: безкоштовний user_data + 1 ключ у search_volume."""
    out = {"configured": bool(config.DATAFORSEO_LOGIN and config.DATAFORSEO_PASSWORD),
           "login": (config.DATAFORSEO_LOGIN[:3] + "…" + config.DATAFORSEO_LOGIN[-8:]) if config.DATAFORSEO_LOGIN else ""}
    if not out["configured"]:
        out["error"] = "Не задано DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD (або сервіс не перезапущено після додавання змінних)"
        return out
    try:
        async with httpx.AsyncClient(timeout=30, auth=(config.DATAFORSEO_LOGIN, config.DATAFORSEO_PASSWORD)) as cl:
            r = await cl.get("https://api.dataforseo.com/v3/appendix/user_data")
        d = r.json() if "json" in r.headers.get("content-type", "") else {}
        out["auth_http"] = r.status_code
        out["auth_status"] = f"{d.get('status_code')} {d.get('status_message')}"
        res = ((d.get("tasks") or [{}])[0].get("result") or [{}])[0] or {}
        out["balance"] = (res.get("money") or {}).get("balance")
    except Exception as e:  # noqa: BLE001
        out["error"] = f"user_data: {e}"
        return out
    try:
        vol = await search_volume(["seo просування"], "ua")
        out["test_volume"] = vol
    except Exception as e:  # noqa: BLE001
        out["error"] = f"search_volume: {e}"
    return out


BAD_CHARS = re.compile(r"[!@%^*()=\{\}\[\];:~`<>?\\|,\"«»“”„#$№/+]")


def gads_clean(k: str) -> str:
    """Ключ у вигляді, який приймає Google Ads (без заборонених символів)."""
    return " ".join(BAD_CHARS.sub(" ", str(k)).lower().split())


def norm_key(k: str) -> str:
    """Ключ для зіставлення відповіді DataForSEO з запитом: регістр, ё→е, апострофи, пробіли."""
    k = gads_clean(k).replace("ё", "е").replace("’", "'").replace("ʼ", "'").replace("`", "'")
    return " ".join(re.sub(r"[^\w' -]", " ", k).split())


async def _volumes_batch(kws: list[str], lang: str, log=None) -> dict[str, int]:
    """Один запит search_volume; при помилці — без мови, а далі ділимо пачку навпіл, щоб один «поганий» ключ не зірвав усі."""
    base = {"keywords": kws, "location_code": config.UA_LOCATION_CODE}
    try:
        res = await _dfs("search_volume", {**base, "language_code": HL.get(lang, "uk")})
    except RuntimeError as e:
        if log:
            log(f"{lang.upper()}: DataForSEO відхилив запит ({e}) — пробую без прив'язки до мови")
        try:
            res = await _dfs("search_volume", base)
        except RuntimeError as e2:
            if len(kws) <= 1:
                if log:
                    log(f"{lang.upper()}: ключ «{kws[0] if kws else ''}» Google Ads не приймає ({e2})")
                return {}
            mid = len(kws) // 2
            out = await _volumes_batch(kws[:mid], lang, log)
            out.update(await _volumes_batch(kws[mid:], lang, log))
            return out
    return {norm_key(i.get("keyword", "")): int(i.get("search_volume") or 0) for i in res if i.get("keyword")}


async def search_volume(keywords: list[str], lang: str, log=None) -> dict[str, int]:
    """{ключ (як передали): середньомісячна частотність в Україні}.
    Спершу кеш (VOLUME_CACHE_DAYS), у DataForSEO йдуть лише ключі, яких у кеші немає."""
    if config.MOCK:
        return {k: (len(k) * 7) % 120 for k in keywords}
    if not dfs_enabled():
        return {}
    from . import db
    clean = {}
    for k in keywords:
        c = gads_clean(k)
        if c and len(c) <= 80 and len(c.split()) <= 10:
            clean.setdefault(c, []).append(k)
    cached = db.cached_volumes(list(clean), lang, config.VOLUME_CACHE_DAYS)
    todo = [c for c in clean if c not in cached]
    fresh_norm = {}
    for i in range(0, len(todo), 1000):
        fresh_norm.update(await _volumes_batch(todo[i:i + 1000], lang, log))
    fresh = {c: fresh_norm.get(norm_key(c), 0) for c in todo}  # не повернуті Google Ads — нуль, щоб не платити вдруге
    if log and todo:
        got = sum(1 for c in todo if norm_key(c) in fresh_norm)
        log(f"{lang.upper()}: DataForSEO повернув {got} з {len(todo)} ключів" + (f" (+{len(cached)} з кешу)" if cached else ""))
    if fresh_norm:  # не кешуємо нулі, якщо запит повністю провалився
        db.save_volumes(fresh, lang)
    vols = {**cached, **(fresh if fresh_norm else {})}
    return {orig: vols[c] for c, origs in clean.items() for orig in origs if c in vols}


async def keyword_ideas(seeds: list[str], lang: str) -> dict[str, int]:
    """Ідеї ключів Google Ads за списком сидів (до 20) з частотністю."""
    if config.MOCK:
        return {f"{s} ціна": 20 for s in seeds[:5]}
    if not dfs_enabled():
        return {}
    res = await _dfs("keywords_for_keywords", {"keywords": _clean(seeds)[:20], "location_code": config.UA_LOCATION_CODE,
                                                "language_code": HL.get(lang, "uk"), "sort_by": "relevance"})
    return {i.get("keyword", ""): int(i.get("search_volume") or 0) for i in res if i.get("keyword")}


async def autocomplete(query: str, lang: str) -> list[str]:
    if config.MOCK:
        return [f"{query} ціна", f"{query} київ", f"{query} під ключ"]
    if not config.SERPER_API_KEY:
        return []
    async with httpx.AsyncClient(timeout=20) as cl:
        r = await cl.post("https://google.serper.dev/autocomplete",
                          json={"q": query, "gl": "ua", "hl": HL.get(lang, "uk")},
                          headers={"X-API-KEY": config.SERPER_API_KEY})
        if r.status_code >= 400:
            return []
        data = r.json()
    return [s.get("value", "") for s in data.get("suggestions", []) if s.get("value")]
