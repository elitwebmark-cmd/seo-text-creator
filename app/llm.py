"""Виклик Claude API для складання ТЗ."""
import time

import anthropic

from . import config
from .prompts import SYSTEM, TOOL, build_user_prompt


def _client():
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("Не задано ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=3, timeout=600)


# ---------------------------------------------------------------- облік витрат
import contextvars  # noqa: E402

JOB = contextvars.ContextVar("job", default=None)
# $ за 1 млн токенів: (вхід, вихід). Запис у кеш = 1.25 × вхід, читання з кешу = 0.1 × вхід.
PRICES = [("opus", 5.0, 25.0), ("haiku", 1.0, 5.0), ("sonnet", 3.0, 15.0)]


def price(model: str):
    m = (model or "").lower()
    return next(((i, o) for k, i, o in PRICES if k in m), (3.0, 15.0))


def _track(step: str, model: str, usage):
    if usage is None:
        return
    pin, pout = price(model)
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    usd = (inp * pin + out * pout + cw * pin * 1.25 + cr * pin * 0.1) / 1e6
    jid = JOB.get()
    if jid:
        try:
            from . import db
            db.add_cost(jid, step or "інше", model, inp, out, cw, cr, usd)
        except Exception:  # noqa: BLE001
            pass
    return usd


def _system(system: str, cache: bool):
    return [{"type": "text", "text": system, **({"cache_control": {"type": "ephemeral"}} if cache else {})}]


def _content(prompt, cache: bool = True):
    """prompt: рядок або список частин; частина (text, True) кешується (стабільний префікс, напр. бриф)."""
    if isinstance(prompt, str):
        return prompt
    out = []
    for part in prompt:
        text, c = (part, False) if isinstance(part, str) else part
        if text:
            out.append({"type": "text", "text": text, **({"cache_control": {"type": "ephemeral"}} if (c and cache) else {})})
    return out


def _api_error(e):
    body = getattr(e, "body", None) or {}
    text = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
    text = text or str(e)
    if "credit balance" in text.lower():
        return RuntimeError("Закінчився баланс Claude API — поповніть у console.anthropic.com → Plans & Billing (Claude API 400)")
    return RuntimeError(f"Claude API {e.status_code}: {text}")


def call_tool(system: str, prompt, tool: dict, max_tokens: int = 20000, check=None, model: str | None = None,
              step: str = "", cache: bool = False) -> dict:
    """Виклик Claude з примусовим інструментом; повертає його input."""
    model = model or config.ANTHROPIC_MODEL
    last = None
    for attempt in range(2):
        try:
            with _client().messages.stream(
                model=model, max_tokens=max_tokens, system=_system(system, cache), tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": _content(prompt, cache)}],
            ) as stream:
                msg = stream.get_final_message()
            _track(step, model, msg.usage)
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
                raise _api_error(e) from None
            last = e
            time.sleep(5 * (attempt + 1))
        except (anthropic.APIConnectionError, ValueError) as e:
            last = e
            time.sleep(3)
    raise RuntimeError(f"Claude API: {last}")


def generate_tz(page: dict, project: dict, research: dict) -> dict:
    if config.MOCK:
        from .mock import mock_tz
        return mock_tz(page)
    data = call_tool(SYSTEM, build_user_prompt(page, project, research), TOOL, check=lambda d: bool(d.get("blocks")), step="ТЗ")
    from .norms import fix_generated
    return fix_generated(normalize(data))


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


def call_text(system: str, prompt, max_tokens: int = 16000, model: str | None = None, step: str = "",
              cache: bool = False, min_len: int = 200, on_truncate: str = "continue") -> str:
    """Звичайна текстова відповідь Claude.
    Якщо відповідь обрізано лімітом токенів:
      on_truncate="continue" — дописуємо продовження (до 2 разів), оплачується лише нова частина;
      on_truncate="partial"  — повертаємо те, що встигло згенеруватися (напр. правки редактора);
      on_truncate="error"    — помилка."""
    model = model or config.ANTHROPIC_MODEL
    messages = [{"role": "user", "content": _content(prompt, cache)}]
    text, last, conts = "", None, 0
    attempt = 0
    while attempt < 2:
        try:
            with _client().messages.stream(model=model, max_tokens=max_tokens, system=_system(system, cache),
                                           messages=messages) as stream:
                msg = stream.get_final_message()
            _track(step, model, msg.usage)
            text += "".join(b.text for b in msg.content if b.type == "text")
            if msg.stop_reason == "max_tokens":
                if on_truncate == "partial":
                    return text.strip()
                if on_truncate == "continue" and conts < 2:
                    conts += 1
                    messages = [messages[0], {"role": "assistant", "content": text.rstrip()}]
                    continue
                raise RuntimeError(f"Відповідь Claude обрізано лімітом токенів (крок: {step or 'текст'})")
            text = text.strip()
            if len(text) < min_len:
                raise ValueError("Порожня відповідь моделі")
            return text
        except anthropic.APIStatusError as e:
            if e.status_code < 429 or e.status_code == 404:
                raise _api_error(e) from None
            last = e
            attempt += 1
            time.sleep(5 * attempt)
        except (anthropic.APIConnectionError, ValueError) as e:
            last = e
            attempt += 1
            text, messages, conts = "", [messages[0]], 0
            time.sleep(3)
    raise RuntimeError(f"Claude API: {last}")
