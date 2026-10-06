"""Завантаження та розбір сторінок конкурентів."""
import asyncio
import re
from urllib.parse import urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

from . import config

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
PRICE_RE = re.compile(r"(?:від|от|from)?\s?\$?\s?\d[\d\s.,]{1,9}\s?(?:грн|₴|\$|usd|€|eur|долар|доллар)", re.I)


def domain(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def skip(url: str, own_domain: str = "") -> bool:
    d = domain(url)
    if url.lower().endswith(".pdf"):
        return True
    return any(d == s or d.endswith("." + s) for s in config.SKIP_DOMAINS)


def _headings(soup: BeautifulSoup):
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    hs = []
    for h in soup.find_all(["h1", "h2", "h3"]):
        t = re.sub(r"\s+", " ", h.get_text(" ", strip=True))
        if 2 < len(t) < 160:
            hs.append((h.name.upper(), t))
    return hs


def parse_html(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    md = soup.find("meta", attrs={"name": "description"})
    desc = md.get("content", "").strip() if md else ""
    text = trafilatura.extract(html, include_tables=True, include_comments=False, favor_recall=True) or ""
    hs = _headings(BeautifulSoup(html, "lxml"))
    if len(text) < 1200:
        body = soup.body.get_text(" ", strip=True) if soup.body else ""
        text = body if len(body) > len(text) else text
    text = re.sub(r"[ \t]+", " ", text)
    faq = [t for _, t in hs if t.endswith("?")]
    prices = list(dict.fromkeys(m.group(0).strip() for m in PRICE_RE.finditer(text)))[:8]
    is_blog = bool(re.search(r"/(blog|blog-uk|news|stati|statti|article|articles|wiki|baza-znan)/", url.lower()))
    return {
        "url": url,
        "domain": domain(url),
        "title": title,
        "description": desc,
        "headings": hs[:80],
        "text": text,
        "chars": len(re.sub(r"\s+", " ", text)),
        "faq": faq[:15],
        "prices": prices,
        "type_guess": "стаття" if is_blog else "послуга/лендинг",
    }


async def fetch(url: str, client: httpx.AsyncClient) -> dict:
    try:
        r = await client.get(url, headers={"User-Agent": UA, "Accept-Language": "uk,ru;q=0.8,en;q=0.5"})
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
            return {"url": url, "domain": domain(url), "error": f"HTTP {r.status_code}"}
        html = r.text[:3_000_000]
        return await asyncio.to_thread(parse_html, url, html)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "domain": domain(url), "error": type(e).__name__}


async def fetch_many(urls: list[str]) -> list[dict]:
    if config.MOCK:
        from .mock import mock_pages
        return mock_pages(urls)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cl:
        return await asyncio.gather(*(fetch(u, cl) for u in urls))
