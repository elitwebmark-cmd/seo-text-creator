"""Просте сховище задач у SQLite."""
import json
import sqlite3
import threading
import time
import uuid

from .config import DATA_DIR

_DB = DATA_DIR / "jobs.sqlite3"
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, created REAL, title TEXT, status TEXT,
                progress REAL, log TEXT, params TEXT, files TEXT, error TEXT)"""
        )
        # задачі, перервані перезапуском сервера
        c.execute("UPDATE jobs SET status='error', error='Перервано перезапуском сервера — запустіть ще раз' "
                  "WHERE status IN ('queued','running')")


def _vol_table(c):
    c.execute("CREATE TABLE IF NOT EXISTS volumes(keyword TEXT, lang TEXT, volume INTEGER, ts REAL, PRIMARY KEY(keyword, lang))")


def cached_volumes(keywords, lang, days):
    if not keywords:
        return {}
    since = time.time() - days * 86400
    out = {}
    with _lock, _conn() as c:
        _vol_table(c)
        for i in range(0, len(keywords), 500):
            part = keywords[i:i + 500]
            q = f"SELECT keyword, volume FROM volumes WHERE lang=? AND ts>=? AND keyword IN ({','.join('?' * len(part))})"
            for r in c.execute(q, (lang, since, *part)):
                out[r["keyword"]] = r["volume"]
    return out


def save_volumes(vols: dict, lang: str):
    now = time.time()
    with _lock, _conn() as c:
        _vol_table(c)
        c.executemany("INSERT OR REPLACE INTO volumes VALUES(?,?,?,?)", [(k, lang, int(v), now) for k, v in vols.items()])


def _cost_table(c):
    c.execute("CREATE TABLE IF NOT EXISTS costs(job TEXT, step TEXT, model TEXT, inp INTEGER, out INTEGER, "
              "cache_w INTEGER, cache_r INTEGER, usd REAL, ts REAL)")


def add_cost(jid, step, model, inp, out, cw, cr, usd):
    with _lock, _conn() as c:
        _cost_table(c)
        c.execute("INSERT INTO costs VALUES(?,?,?,?,?,?,?,?,?)", (jid, step, model, inp, out, cw, cr, usd, time.time()))


def job_costs(jid) -> dict:
    """{'usd', 'calls', 'steps': [{step, model, calls, inp, out, cache_r, usd}]}"""
    with _lock, _conn() as c:
        _cost_table(c)
        rows = c.execute("SELECT step, model, COUNT(*) n, SUM(inp) i, SUM(out) o, SUM(cache_r) r, SUM(cache_w) w, SUM(usd) u "
                         "FROM costs WHERE job=? GROUP BY step, model ORDER BY u DESC", (jid,)).fetchall()
    steps = [{"step": r["step"], "model": r["model"], "calls": r["n"], "inp": r["i"], "out": r["o"], "cache_r": r["r"],
              "cache_w": r["w"], "usd": round(r["u"] or 0, 4)} for r in rows]
    return {"usd": round(sum(x["usd"] for x in steps), 4), "calls": sum(x["calls"] for x in steps), "steps": steps}


def costs_total(jids) -> dict:
    if not jids:
        return {}
    with _lock, _conn() as c:
        _cost_table(c)
        q = f"SELECT job, SUM(usd) u FROM costs WHERE job IN ({','.join('?' * len(jids))}) GROUP BY job"
        return {r["job"]: round(r["u"] or 0, 4) for r in c.execute(q, list(jids))}


def create(title: str, params: dict) -> str:
    jid = uuid.uuid4().hex[:12]
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
            (jid, time.time(), title, "queued", 0.0, "[]", json.dumps(params, ensure_ascii=False), "[]", ""),
        )
    return jid


def get(jid: str):
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("log", "files", "params"):
        d[k] = json.loads(d[k] or "null")
    return d


def list_jobs(limit: int = 50):
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT id, created, title, status, progress, files, error, params FROM jobs ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["files"] = json.loads(d["files"] or "[]")
        d["kind"] = (json.loads(d.pop("params") or "{}") or {}).get("kind", "tz")
        out.append(d)
    totals = costs_total([d["id"] for d in out])
    for d in out:
        d["cost_usd"] = totals.get(d["id"], 0)
    return out


def update(jid: str, **kw):
    if not kw:
        return
    for k in ("files",):
        if k in kw:
            kw[k] = json.dumps(kw[k], ensure_ascii=False)
    sets = ", ".join(f"{k}=?" for k in kw)
    with _lock, _conn() as c:
        c.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*kw.values(), jid))


def log(jid: str, msg: str):
    with _lock, _conn() as c:
        r = c.execute("SELECT log FROM jobs WHERE id=?", (jid,)).fetchone()
        items = json.loads(r["log"]) if r else []
        items.append({"t": time.strftime("%H:%M:%S"), "m": msg})
        c.execute("UPDATE jobs SET log=? WHERE id=?", (json.dumps(items[-300:], ensure_ascii=False), jid))


def delete(jid: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (jid,))
