"""Виклик Claude API для складання ТЗ."""
import time

import anthropic

from . import config
from .prompts import SYSTEM, TOOL, build_user_prompt


def _client():
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("Не задано ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=3, timeout=600)


def call_tool(system: str, prompt: str, tool: dict, max_tokens: int = 20000, check=None) -> dict:
    """Виклик Claude з примусовим інструментом; повертає його input."""
    last = None
    for attempt in range(3):
        try:
            with _client().messages.stream(
                model=config.ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                system=system,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                msg = stream.get_final_message()
            for block in msg.content:
                if block.type == "tool_use" and block.name == tool["name"]:
                    data = dict(block.input)
                    if check and not check(data):
                        raise ValueError("Модель повернула неповну відповідь")
                    return data
            raise ValueError(f"Модель не повернула {tool['name']}")
        except anthropic.APIStatusError as e:
            # 400/401/403 (баланс, ключ, доступ до моделі) — повтор не допоможе
            if e.status_code < 429 or e.status_code == 404:
                msg = getattr(e, "body", None) or {}
                text = (msg.get("error") or {}).get("message") if isinstance(msg, dict) else None
                raise RuntimeError(f"Claude API {e.status_code}: {text or e}") from None
            last = e
            time.sleep(5 * (attempt + 1))
        except (anthropic.APIConnectionError, ValueError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Claude API: {last}")


def generate_tz(page: dict, project: dict, research: dict) -> dict:
    if config.MOCK:
        from .mock import mock_tz
        return mock_tz(page)
    data = call_tool(SYSTEM, build_user_prompt(page, project, research), TOOL, check=lambda d: bool(d.get("blocks")))
    return normalize(data)


def normalize(d: dict) -> dict:
    """Приводимо до формату, з яким працюють білдери Excel."""
    blocks = []
    for b in d.get("blocks", []):
        t = b.get("type")
        if t == "c":
            blocks.append(("c", b.get("text") or b.get("ua") or b.get("ru") or ""))
        elif t in ("cta", "faq"):
            blocks.append((t,))
        elif t in ("h1", "h2", "h3", "q", "t"):
            blocks.append((t, b.get("ru", ""), b.get("ua", "")))
    out = {k: d.get(k, "") for k in ("short", "name_ru", "name_ua", "template", "intent", "url_ru", "url_ua",
                                     "volume", "top_note", "title_ru", "desc_ru", "title_ua", "desc_ua", "risks")}
    out["blocks"] = blocks
    for k in ("kw_ru", "kw_ua", "lsi_ru", "lsi_ua"):
        out[k] = [(x.get("k", ""), x.get("f", "")) for x in d.get(k, []) if x.get("k")]
    out["competitors"] = list(d.get("competitors", []))
    out["short"] = (out["short"] or out["name_ua"] or out["name_ru"] or "Сторінка")[:20]
    return out


def call_text(system: str, prompt: str, max_tokens: int = 16000) -> str:
    """Звичайна текстова відповідь Claude (для написання та редагування текстів)."""
    last = None
    for attempt in range(3):
        try:
            with _client().messages.stream(
                model=config.ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                msg = stream.get_final_message()
            text = "".join(b.text for b in msg.content if b.type == "text").strip()
            if len(text) < 200:
                raise ValueError("Порожня відповідь моделі")
            if msg.stop_reason == "max_tokens":
                raise ValueError("Відповідь обрізано лімітом токенів")
            return text
        except anthropic.APIStatusError as e:
            if e.status_code < 429 or e.status_code == 404:
                body = getattr(e, "body", None) or {}
                text = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
                raise RuntimeError(f"Claude API {e.status_code}: {text or e}") from None
            last = e
            time.sleep(5 * (attempt + 1))
        except (anthropic.APIConnectionError, ValueError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Claude API: {last}")
