"""Узгодження норм ключів/LSI: дублі за основою, входження в заголовках ТЗ, реалістичні діапазони."""
import re

from .analysis import phrase_regex, stem


def parse_range(f):
    s = str(f).split("(")[0]
    nums = [int(x) for x in re.findall(r"\d+", s)] or [int(x) for x in re.findall(r"\d+", str(f))]
    if not nums:
        return None
    return (nums[0], nums[1]) if len(nums) > 1 else (nums[0], nums[0])


def alts(k: str):
    k = re.sub(r"\(.*?\)", "", k)
    return [a.strip() for a in re.split(r"\s+/\s+|/", k) if a.strip()]


def count(text: str, k: str) -> int:
    """Входження будь-якої з альтернатив; перекриття рахуються один раз."""
    spans = sorted(m.span() for a in alts(k) for m in phrase_regex(a).finditer(text or ""))
    n, end = 0, -1
    for s0, s1 in spans:
        if s0 >= end:
            n += 1
            end = s1
        else:
            end = max(end, s1)
    return n


def same(a: str, b: str) -> bool:
    """Чи це одне й те саме слово/фраза з точністю до словоформи (взаємне входження)."""
    ta, tb = " / ".join(alts(a)), " / ".join(alts(b))
    return count(tb, a) > 0 and count(ta, b) > 0


def heading_text(tz: dict, lang: str) -> str:
    L = 0 if lang == "ru" else 1
    return " . ".join(b[1 + L] for b in tz.get("blocks", []) if b[0] in ("h1", "h2", "h3", "q") and len(b) > 1)


def volume_range(tz: dict):
    return parse_range(str(tz.get("volume", "")).replace(" ", "").replace(" ", ""))


def effective(tz: dict, lang: str):
    """[(kind, label, lo, hi, original_norm, in_headings, note)] — норми, яких реально можна дотриматися."""
    heads = heading_text(tz, lang)
    out = []
    for kind, items in (("ключ", tz.get(f"kw_{lang}", [])), ("LSI", tz.get(f"lsi_{lang}", []))):
        for k, f in items:
            r = parse_range(f)
            if not r or not k.strip():
                continue
            lo, hi = r
            prev = next((i for i, o in enumerate(out) if same(o[1], k)), None)
            if prev is not None:  # «канал (варіації)» і «каналів» — одне й те саме слово
                out[prev] = (*out[prev][:6], (out[prev][6] + f"; об’єднано з «{k}»").strip("; "))
                continue
            h = count(heads, k)
            note = ""
            if h > lo:
                lo = min(h, hi) if h <= hi else h
            if h >= hi:
                hi = h + max(1, (r[1] - r[0]) // 2 or 1)
                note = f"у заголовках ТЗ уже {h} — норму піднято до {lo}–{hi}"
            out.append((kind, k, lo, hi, f, h, note))
    return out


def fix_generated(tz: dict) -> dict:
    """Пост-обробка ТЗ від Claude: прибрати дублі LSI, підняти норми під заголовки, обмежити нереальні діапазони LSI."""
    vr = volume_range(tz)
    vol_hi = vr[1] if vr else 8000
    for lang in ("ru", "ua"):
        heads = heading_text(tz, lang)
        kws = [k for k, _ in tz.get(f"kw_{lang}", [])]
        new_lsi = []
        for k, f in tz.get(f"lsi_{lang}", []):
            r = parse_range(f)
            if not r or any(same(k, x) for x in kws) or any(same(k, x) for x, _ in new_lsi):
                continue
            lo, hi = r
            cap = max(4, round(vol_hi / (650 if len(alts(k)[0].split()) == 1 else 1200)))
            hi = min(hi, cap)
            lo = min(lo, max(1, round(hi * 0.6)))
            h = count(heads, k)
            if h >= hi:
                hi = h + 2
                lo = max(lo, h)
            new_lsi.append((k, f"{lo}-{hi}" if lo != hi else str(lo)))
        tz[f"lsi_{lang}"] = new_lsi
        new_kw = []
        for k, f in tz.get(f"kw_{lang}", []):
            r = parse_range(f)
            h = count(heads, k)
            if r and h >= r[1]:
                tail = re.sub(r"^[\d\s\-–]+", "", str(f)).strip()
                f = f"{h}-{h + 1}" + (f" {tail}" if tail else "") + " (з урахуванням заголовків)"
            new_kw.append((k, f))
        tz[f"kw_{lang}"] = new_kw
    return tz
