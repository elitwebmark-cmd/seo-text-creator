"""Тестовий режим без зовнішніх API (MOCK=1)."""
import copy
import time

from .prompts import EXAMPLE
from .llm import normalize


def mock_serp(query, lang):
    return {
        "query": query, "lang": lang,
        "organic": [{"position": i + 1, "title": f"Сторінка {i+1} про {query}", "url": f"https://example{i+1}.com.ua/{lang}/page", "snippet": ""} for i in range(8)],
        "paa": [f"Що таке {query}?", f"Скільки коштує {query}?"],
        "related": [f"{query} ціна", f"{query} київ"],
    }


def mock_pages(urls):
    text = ("Ми надаємо послуги з управління репутацією, працюємо з відгуками, бренд і видача. " * 120)
    return [{"url": u, "domain": u.split('/')[2], "title": "Mock", "description": "", "type_guess": "послуга/лендинг",
             "headings": [("H1", "Заголовок"), ("H2", "Що входить"), ("H2", "Ціни"), ("H2", "FAQ")],
             "text": text, "chars": len(text), "faq": ["Скільки коштує?"], "prices": ["від 500 $"]} for u in urls]


def mock_tz(page):
    time.sleep(1)
    d = copy.deepcopy(EXAMPLE)
    name = page.get("name_ua") or page.get("name_ru") or "Сторінка"
    d["short"] = name[:20]
    d["name_ua"] = page.get("name_ua") or name
    d["name_ru"] = page.get("name_ru") or name
    d["kw_ua"] = [{"k": k, "f": "1-2"} for k, _ in (page.get("ua") or [])]
    d["kw_ru"] = [{"k": k, "f": "1-2"} for k, _ in (page.get("ru") or [])]
    return normalize(d)
