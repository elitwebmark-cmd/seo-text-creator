"""Видача Google через Serper.dev."""
import httpx

from . import config

HL = {"ua": "uk", "ru": "ru"}


async def search(query: str, lang: str, num: int = 10) -> dict:
    if config.MOCK:
        from .mock import mock_serp
        return mock_serp(query, lang)
    if not config.SERPER_API_KEY:
        raise RuntimeError("Не задано SERPER_API_KEY")
    payload = {"q": query, "gl": "ua", "hl": HL.get(lang, "uk"), "num": num}
    async with httpx.AsyncClient(timeout=30) as cl:
        r = await cl.post(
            "https://google.serper.dev/search",
            json=payload,
            headers={"X-API-KEY": config.SERPER_API_KEY, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    return {
        "query": query,
        "lang": lang,
        "organic": [
            {"position": o.get("position"), "title": o.get("title", ""), "url": o.get("link", ""), "snippet": o.get("snippet", "")}
            for o in data.get("organic", [])
        ][:num],
        "paa": [q.get("question", "") for q in data.get("peopleAlsoAsk", []) if q.get("question")],
        "related": [q.get("query", "") for q in data.get("relatedSearches", []) if q.get("query")],
    }
