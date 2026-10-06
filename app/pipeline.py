"""Оркестрація: дослідження ТОПу → ТЗ від Claude → Excel."""
import asyncio
import json
import traceback

from . import analysis, config, db, excel, llm, scraper, serp

LANG_NAME = {"ua": "UA", "ru": "RU"}


def pick_queries(keywords):
    """1–2 головні запити кластера."""
    if not keywords:
        return []
    qs = [keywords[0][0]]
    if len(keywords) > 1 and keywords[1][1] >= max(10, keywords[0][1] * 0.3):
        qs.append(keywords[1][0])
    return qs


def own_host(domain: str) -> str:
    d = (domain or "").lower().replace("https://", "").replace("http://", "").strip("/")
    return d[4:] if d.startswith("www.") else d


async def research_lang(keywords, lang, project, log):
    queries = pick_queries(keywords)
    serps = []
    for q in queries:
        log(f"{LANG_NAME[lang]}: видача за «{q}»")
        serps.append(await serp.search(q, lang))
    own = own_host(project.get("domain", ""))
    own_in, urls = [], []
    for s in serps:
        for o in s["organic"]:
            u = o["url"]
            if own and own in scraper.domain(u):
                own_in.append(u)
                continue
            if scraper.skip(u) or u in urls:
                continue
            urls.append(u)
    urls = urls[: config.MAX_COMPETITORS]
    log(f"{LANG_NAME[lang]}: розбір {len(urls)} сторінок ТОПу")
    pages = await scraper.fetch_many(urls)
    good = [p for p in pages if not p.get("error") and p.get("chars", 0) > 800]
    vol = analysis.volume_stats(good)
    target = max(3000, min(14000, int(vol["median"] * 0.9 / 500) * 500 or 6000))
    kw_stats = analysis.keyword_stats(good, [k for k, _ in keywords[:8]])
    lsi = analysis.lsi_candidates(good, target)
    slim = [{k: v for k, v in p.items() if k != "text"} for p in pages]
    return {"queries": queries, "serps": serps, "pages": slim, "volume": vol, "target_chars": target,
            "kw_stats": kw_stats, "lsi": lsi, "own_in_serp": list(dict.fromkeys(own_in))}


async def process_page(jid, idx, page, project, outdir, sem_lock, done_cb):
    pid = f"{idx:02d}"
    title = page.get("name_ua") or page.get("name_ru") or pid
    def log(m):
        db.log(jid, f"[{pid} {title}] {m}")
    async with sem_lock:
        research = {}
        for lang in ("ua", "ru"):
            if page.get(lang):
                research[lang] = await research_lang(page[lang], lang, project, log)
        log("Claude складає ТЗ…")
        tz = await asyncio.to_thread(llm.generate_tz, page, project, research)
        item = {"id": pid, "tz": tz, "sem": {l: page.get(l) or [] for l in ("ua", "ru")}, "research": research}
        (outdir / f"page_{pid}.json").write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")
        log("готово")
        done_cb()
        return item


async def run_job(jid: str):
    job = db.get(jid)
    params = job["params"]
    project, pages, formats = params["project"], params["pages"], params["formats"]
    outdir = config.DATA_DIR / "jobs" / jid
    outdir.mkdir(parents=True, exist_ok=True)
    db.update(jid, status="running", progress=0.02)
    db.log(jid, f"Старт: {len(pages)} сторінок, формати: {', '.join(formats)}")
    sem_lock = asyncio.Semaphore(max(1, config.PARALLEL_CLUSTERS))
    done = {"n": 0}

    def tick():
        done["n"] += 1
        db.update(jid, progress=round(0.05 + 0.85 * done["n"] / len(pages), 3))

    results = await asyncio.gather(
        *(process_page(jid, i + 1, p, project, outdir, sem_lock, tick) for i, p in enumerate(pages)),
        return_exceptions=True,
    )
    items, errors = [], []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            name = pages[i].get("name_ua") or pages[i].get("name_ru")
            errors.append(f"{name}: {r}")
            db.log(jid, f"ПОМИЛКА [{i+1:02d} {name}]: {r}")
        else:
            items.append(r)
    files = []
    if items:
        slug = (project.get("domain") or "tz").replace("https://", "").replace("/", "")[:40]
        if "advanced" in formats:
            fn = f"TZ_Advanced_{slug}.xlsx"
            await asyncio.to_thread(excel.build_advanced, items, project, outdir / fn)
            files.append(fn)
        if "base" in formats:
            fn = f"TZ_Base_{slug}.xlsx"
            await asyncio.to_thread(excel.build_base, items, outdir / fn)
            files.append(fn)
    status = "done" if items and not errors else ("partial" if items else "error")
    db.update(jid, status=status, progress=1.0, files=files, error="\n".join(errors))
    db.log(jid, "Завершено" if items else "Не вдалося згенерувати жодного ТЗ")


async def run_semantics_job(jid: str):
    from . import collect
    job = db.get(jid)
    params = job["params"]
    entries, langs, project = params["entries"], params["langs"], params.get("project", {})
    outdir = config.DATA_DIR / "jobs" / jid
    outdir.mkdir(parents=True, exist_ok=True)
    db.update(jid, status="running", progress=0.03)
    db.log(jid, f"Збір семантики: {len(entries)} послуг/сторінок, мови: {', '.join(l.upper() for l in langs)}")
    results = []
    for i, e in enumerate(entries):
        results += await collect.run([e], langs, project, lambda m: db.log(jid, m))
        db.update(jid, progress=round(0.05 + 0.9 * (i + 1) / len(entries), 3))
    if not results:
        db.update(jid, status="error", progress=1.0, error="Не вдалося зібрати семантику")
        return
    (outdir / "semantics.json").write_text(collect.dumps(results), encoding="utf-8")
    fn = "Semantics.xlsx"
    await asyncio.to_thread(collect.semantics_xlsx, results, outdir / fn)
    status = "done" if len(results) == len(entries) else "partial"
    db.update(jid, status=status, progress=1.0, files=[fn])
    db.log(jid, "Семантику зібрано. Натисніть «Взяти в роботу», щоб перейти до ТЗ.")


def start(jid: str):
    """Запуск у фоні (окремий event loop у потоці)."""
    import threading

    def _runner():
        try:
            kind = (db.get(jid)["params"] or {}).get("kind", "tz")
            asyncio.run(run_semantics_job(jid) if kind == "semantics" else run_job(jid))
        except Exception as e:  # noqa: BLE001
            db.update(jid, status="error", error=str(e))
            db.log(jid, "Фатальна помилка: " + "".join(traceback.format_exception_only(type(e), e)))

    threading.Thread(target=_runner, daemon=True).start()
