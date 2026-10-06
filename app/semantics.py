"""Розбір семантики: xlsx (аркуші ua/ru або колонка «Мова») і текст."""
import difflib
import io
import re

import openpyxl

LANGS = ("ua", "ru")


def _lang_from(name: str):
    n = (name or "").strip().lower()
    if n in ("ua", "uk", "укр", "ukr", "україна", "украинский", "українська"):
        return "ua"
    if n in ("ru", "рус", "rus", "русский", "російська"):
        return "ru"
    return None


def _vol(v):
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(" ", "")
    m = re.match(r"^(\d+)", s)
    return int(m.group(1)) if m else 0


def _find_cols(rows):
    """Шукаємо рядок-заголовок і індекси колонок."""
    for i, row in enumerate(rows[:15]):
        low = [str(c).strip().lower() if c is not None else "" for c in row]
        kc = next((j for j, v in enumerate(low) if v.startswith(("ключ", "keyword", "запит", "фраза"))), None)
        if kc is None:
            continue
        cc = next((j for j, v in enumerate(low) if v.startswith(("кластер", "cluster", "група", "группа", "сторінка", "страница"))), None)
        vc = next((j for j, v in enumerate(low) if v.startswith(("частот", "volume", "обсяг", "объем", "vol"))), None)
        lc = next((j for j, v in enumerate(low) if v.startswith(("мова", "язык", "lang"))), None)
        return i, cc, kc, vc, lc
    return None


def parse_xlsx(data: bytes):
    """Повертає {'ua': [{'name','keywords':[(kw,vol)]}], 'ru': [...]}."""
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = {"ua": [], "ru": []}
    sheets = list(wb.worksheets)
    lang_sheets = [ws for ws in sheets if _lang_from(ws.title)]
    use = lang_sheets or sheets
    for ws in use:
        sheet_lang = _lang_from(ws.title)
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        hdr = _find_cols(rows)
        if not hdr:
            continue
        hi, cc, kc, vc, lc = hdr
        current = {}
        for row in rows[hi + 1:]:
            row = row + [None] * 10
            kw = row[kc]
            cl = row[cc] if cc is not None else None
            lang = sheet_lang or (_lang_from(str(row[lc])) if lc is not None and row[lc] else None) or "ua"
            if (kw is None or str(kw).strip() == "") and cl:
                current[lang] = str(cl).strip()
                continue
            if kw is None or str(kw).strip() == "":
                continue
            name = str(cl).strip() if cl else current.get(lang, "Без кластера")
            current[lang] = name
            bucket = next((c for c in out[lang] if c["name"] == name), None)
            if bucket is None:
                bucket = {"name": name, "keywords": []}
                out[lang].append(bucket)
            k = str(kw).strip()
            if k.lower() in ("keyword", "ключове слово", "ключевое слово"):
                continue
            if all(k.lower() != x[0].lower() for x in bucket["keywords"]):
                bucket["keywords"].append((k, _vol(row[vc]) if vc is not None else 0))
    for lang in LANGS:
        out[lang] = [c for c in out[lang] if c["keywords"]]
        for c in out[lang]:
            c["keywords"].sort(key=lambda x: -x[1])
    return out


def parse_text(text: str):
    """Формат:
    # Назва кластера
    ключ; 50
    ключ 2 | 10
    """
    clusters, cur = [], None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            cur = {"name": line.lstrip("#").strip() or "Кластер", "keywords": []}
            clusters.append(cur)
            continue
        parts = re.split(r"\s*[;|\t]\s*", line)
        kw = parts[0].strip()
        vol = _vol(parts[1]) if len(parts) > 1 else 0
        if cur is None:
            cur = {"name": kw, "keywords": []}
            clusters.append(cur)
        cur["keywords"].append((kw, vol))
    for c in clusters:
        c["keywords"].sort(key=lambda x: -x[1])
    return [c for c in clusters if c["keywords"]]


_UA2RU = str.maketrans({"і": "и", "ї": "и", "є": "е", "ґ": "г", "'": "", "’": ""})
_LOOK = str.maketrans({"а": "a", "о": "o", "е": "e", "с": "c", "р": "p", "х": "x", "к": "k", "і": "i"})


def _tokens(s: str):
    out = set()
    for t in re.findall(r"[a-zа-яіїєґ0-9]+", s.lower()):
        if re.search(r"[a-z]", t) and re.search(r"[а-яіїєґ]", t):
            t = t.translate(_LOOK)
        t = t.translate(_UA2RU)
        if len(t) >= 2:
            out.add(t[:4])
    return out


def _sig(cluster):
    toks = set()
    for k, _ in cluster["keywords"][:6]:
        toks |= _tokens(k)
    return toks | _tokens(cluster["name"])


def auto_pair(ua: list, ru: list, threshold: float = 0.34):
    """Пари UA<->RU за схожістю основ слів (жадібно, від найкращих пар)."""
    scores = []
    for i, u in enumerate(ua):
        a = _sig(u)
        for j, r in enumerate(ru):
            b = _sig(r)
            s = len(a & b) / max(1, min(len(a), len(b)))
            scores.append((s, i, j))
    scores.sort(reverse=True)
    pair_ua, used_ru = {}, set()
    for s, i, j in scores:
        if s < threshold:
            break
        if i in pair_ua or j in used_ru:
            continue
        pair_ua[i] = j
        used_ru.add(j)
    pages = [{"ua": i, "ru": pair_ua.get(i)} for i in range(len(ua))]
    pages += [{"ua": None, "ru": j} for j in range(len(ru)) if j not in used_ru]
    return pages
