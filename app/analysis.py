"""Метрики сторінок ТОПу: обсяги, входження ключів, кандидати LSI."""
import re
import statistics
from collections import Counter

STOP = set("""
і й та та й а але або чи що як це ці цей ця ці той та ті для від до на в у з із зі по при про без під над між через за же ж би б не ні так також вже ще лише тільки дуже може можна треба потрібно буде бути є був була були ваш ваша ваше ваші наш наша наше наші ми ви вони він вона воно їх його її свій своя своє свої який яка яке які котрий котра коли де тут там чому тому якщо ніж щоб щодо усі всі все весь вся кожен кожна інший інша інші один одна одне перший друга більше менше саме також зокрема
и в во на с со к ко по о об от до за из у же ли не ни что как это эти этот эта тот та те для при про без под над между через или но а да также уже еще ещё лишь только очень может можно нужно будет быть есть был была были ваш ваша ваше ваши наш наша наше наши мы вы они он она оно их его ее её свой своя свое свои который которая которое которые когда где тут там почему поэтому если чем чтобы все всё весь вся каждый другой другая другие один одна одно первый более менее именно
the and for with you your our are this that from have not can will all more
""".split())


def stem(w: str) -> str:
    w = w.lower()
    if len(w) <= 4:
        return w
    return w[: max(4, len(w) - 3)] if len(w) > 7 else w[: max(4, len(w) - 2)]


def phrase_regex(phrase: str) -> re.Pattern:
    words = re.findall(r"[\wʼ'’-]+", phrase.lower())
    parts = []
    for w in words:
        s = re.escape(stem(w)) if len(w) > 4 else re.escape(w)
        parts.append(s + r"[\w-]*")
    return re.compile(r"(?<![\w])" + r"[\s\-–—,:]+".join(parts), re.I)


def count_phrase(text: str, phrase: str) -> int:
    return len(phrase_regex(phrase).findall(text or ""))


def keyword_stats(pages: list[dict], keywords: list[str]) -> list[dict]:
    ok = [p for p in pages if p.get("text")]
    out = []
    for kw in keywords:
        counts = [count_phrase(p["text"], kw) for p in ok]
        per1k = [c / max(1, p["chars"]) * 1000 for c, p in zip(counts, ok)]
        out.append({"keyword": kw, "counts": counts, "per_1000": round(statistics.median(per1k), 3) if per1k else 0})
    return out


def lsi_candidates(pages: list[dict], target_chars: int, top: int = 40) -> list[dict]:
    ok = [p for p in pages if p.get("text") and p.get("chars", 0) > 800]
    if not ok:
        return []
    per_doc = []
    forms: dict[str, Counter] = {}
    for p in ok:
        c = Counter()
        for w in re.findall(r"[a-zа-яіїєґ][a-zа-яіїєґʼ'’-]{2,}", p["text"].lower()):
            if w in STOP or len(w) < 3:
                continue
            s = stem(w)
            c[s] += 1
            forms.setdefault(s, Counter())[w] += 1
        per_doc.append((c, p["chars"]))
    df = Counter()
    for c, _ in per_doc:
        df.update(set(c))
    need = max(2, round(len(ok) * 0.5))
    cands = [s for s, n in df.items() if n >= need]
    rows = []
    for s in cands:
        norm = sorted(c.get(s, 0) / ch * target_chars for c, ch in per_doc)
        total = sum(c.get(s, 0) for c, _ in per_doc)
        lo = norm[max(0, len(norm) // 4)]
        hi = norm[min(len(norm) - 1, (3 * len(norm)) // 4)]
        rows.append({
            "stem": s,
            "word": forms[s].most_common(1)[0][0],
            "docs": df[s],
            "total": total,
            "range": f"{max(1, round(lo))}-{max(1, round(hi))}",
        })
    rows.sort(key=lambda r: (-r["docs"], -r["total"]))
    return rows[:top]


def volume_stats(pages: list[dict]) -> dict:
    lens = [p["chars"] for p in pages if p.get("chars")]
    if not lens:
        return {"median": 0, "min": 0, "max": 0}
    return {"median": int(statistics.median(lens)), "min": min(lens), "max": max(lens)}
