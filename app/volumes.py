"""Частотність і підказки ключів: DataForSEO (Google Ads / Keyword Planner) + Serper Autocomplete."""
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


async def search_volume(keywords: list[str], lang: str) -> dict[str, int]:
    """{ключ: середньомісячна частотність в Україні}.
    Спершу кеш (VOLUME_CACHE_DAYS), у DataForSEO йдуть лише ключі, яких у кеші немає."""
    if config.MOCK:
        return {k: (len(k) * 7) % 120 for k in keywords}
    if not dfs_enabled():
        return {}
    from . import db
    kws = _clean(keywords)
    out = db.cached_volumes(kws, lang, config.VOLUME_CACHE_DAYS)
    kws = [k for k in kws if k not in out]
    fresh = {}
    for i in range(0, len(kws), 1000):
        res = await _dfs("search_volume", {"keywords": kws[i:i + 1000], "location_code": config.UA_LOCATION_CODE,
                                            "language_code": HL.get(lang, "uk")})
        for item in res:
            fresh[item.get("keyword", "")] = int(item.get("search_volume") or 0)
    for k in kws:  # ключі, яких Google Ads не повернув, вважаємо нульовими, щоб не платити за них повторно
        fresh.setdefault(k, 0)
    if fresh:
        db.save_volumes(fresh, lang)
    out.update(fresh)
    return out


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
