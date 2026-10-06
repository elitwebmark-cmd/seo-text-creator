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
    if len(keywords) > 1 and (keywords[1][1] or 0) >= max(10, (keywords[0][1] or 0) * 0.3):
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


async def fill_missing_volumes(jid, pages):
    """DataForSEO викликається ЛИШЕ для ключів без частотності."""
    from . import volumes
    for lang in ("ua", "ru"):
        missing = list(dict.fromkeys(k for p in pages for k, v in (p.get(lang) or []) if v is None))
        if not missing:
            continue
        if not volumes.dfs_enabled():
            db.log(jid, f"{lang.upper()}: {len(missing)} ключів без частотності — DataForSEO не підключено, лишаємо «—»")
            continue
        try:
            vols = await volumes.search_volume(missing, lang)
            db.log(jid, f"{lang.upper()}: дозняв частотність для {len(missing)} ключів без неї (кеш + DataForSEO)")
        except Exception as e:  # noqa: BLE001
            db.log(jid, f"{lang.upper()}: частотність не знято: {e}")
            continue
        norm = {k.lower().strip(): v for k, v in vols.items()}
        for p in pages:
            p[lang] = sorted([(k, norm.get(" ".join(k.lower().split()), v) if v is None else v) for k, v in (p.get(lang) or [])],
                             key=lambda x: -(x[1] or 0))
    if all(v is not None for p in pages for l in ("ua", "ru") for _, v in (p.get(l) or [])):
        db.log(jid, "Частотність є для всіх ключів — DataForSEO не використовувався")


async def run_job(jid: str):
    job = db.get(jid)
    params = job["params"]
    project, pages, formats = params["project"], params["pages"], params["formats"]
    outdir = config.DATA_DIR / "jobs" / jid
    outdir.mkdir(parents=True, exist_ok=True)
    db.update(jid, status="running", progress=0.02)
    db.log(jid, f"Старт: {len(pages)} сторінок, формати: {', '.join(formats)}")
    await fill_missing_volumes(jid, pages)
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
    if items and "texts" in formats:
        db.update(jid, files=files, progress=0.9)
        files += await write_texts(jid, items, project, outdir, errors)
    status = "done" if items and not errors else ("partial" if items else "error")
    db.update(jid, status=status, progress=1.0, files=files, error="\n".join(errors))
    db.log(jid, "Завершено" if items else "Не вдалося згенерувати жодного ТЗ")


async def write_texts(jid, items, project, outdir, errors):
    """SEO-тексти для кожної сторінки × мови → Word."""
    from . import docx_out, writer
    sem = asyncio.Semaphore(max(1, config.PARALLEL_CLUSTERS))
    tasks = [(it, l) for it in items for l in ("ua", "ru") if it["sem"].get(l)]
    done = {"n": 0}
    base = db.get(jid)["progress"] or 0

    async def one(it, lang):
        tz = it["tz"]
        name = tz.get(f"name_{lang}") or tz.get("name_ua") or it["id"]
        async with sem:
            res = await writer.write_text(tz, lang, project, it.get("research"), lambda m: db.log(jid, f"[{it['id']} {name}] {m}"))
        done["n"] += 1
        db.update(jid, progress=round(base + (0.99 - base) * done["n"] / len(tasks), 3))
        return {"id": it["id"], "title": name, "lang": lang, "url": tz.get(f"url_{lang}"), "title_tag": tz.get(f"title_{lang}"),
                "desc": tz.get(f"desc_{lang}"), **res}

    db.log(jid, f"Пишу SEO-тексти: {len(tasks)} шт.")
    res = await asyncio.gather(*(one(it, l) for it, l in tasks), return_exceptions=True)
    texts = []
    for (it, l), r in zip(tasks, res):
        if isinstance(r, Exception):
            errors.append(f"Текст {it['id']} {l.upper()}: {r}")
            db.log(jid, f"ПОМИЛКА тексту {it['id']} {l.upper()}: {r}")
        else:
            texts.append(r)
            (outdir / f"text_{it['id']}_{l}.md").write_text(r["markdown"], encoding="utf-8")
    if not texts:
        return []
    texts.sort(key=lambda t: (t["id"], t["lang"] != "ua"))
    slug = (project.get("domain") or "texts").replace("https://", "").replace("/", "")[:40]
    fn = f"Texts_{slug}.docx"
    await asyncio.to_thread(docx_out.build, texts, outdir / fn)
    bad = [f"{t['id']} {t['lang'].upper()}" for t in texts if t["report"]["issues"]]
    db.log(jid, "Тексти готові" + (f"; потребують ручної перевірки: {', '.join(bad)}" if bad else ", усі пройшли перевірку"))
    return [fn]


async def run_texts_job(jid: str):
    """Тексти за вже згенерованими ТЗ іншої задачі."""
    import json as _json
    params = db.get(jid)["params"]
    src = config.DATA_DIR / "jobs" / params["source"]
    src_job = db.get(params["source"]) or {}
    project = (src_job.get("params") or {}).get("project", {})
    items = [_json.loads(p.read_text(encoding="utf-8")) for p in sorted(src.glob("page_*.json"))]
    for it in items:  # JSON перетворює кортежі на списки
        it["tz"]["blocks"] = [tuple(b) for b in it["tz"]["blocks"]]
    outdir = config.DATA_DIR / "jobs" / jid
    outdir.mkdir(parents=True, exist_ok=True)
    db.update(jid, status="running", progress=0.02)
    if not items:
        db.update(jid, status="error", progress=1.0, error="У вихідній задачі немає готових ТЗ")
        return
    errors = []
    files = await write_texts(jid, items, project, outdir, errors)
    status = "done" if files and not errors else ("partial" if files else "error")
    db.update(jid, status=status, progress=1.0, files=files, error="\n".join(errors))


async def run_volumes_job(jid: str):
    """Лише частотність для готових кластерів → Semantics.xlsx у форматі «кластер / ключ / частотність»."""
    from . import collect
    pages = db.get(jid)["params"]["pages"]
    outdir = config.DATA_DIR / "jobs" / jid
    outdir.mkdir(parents=True, exist_ok=True)
    db.update(jid, status="running", progress=0.1)
    n = sum(len(p.get(l) or []) for p in pages for l in ("ua", "ru"))
    db.log(jid, f"Частотність: {len(pages)} кластерів, {n} ключів")
    await fill_missing_volumes(jid, pages)
    results = [{"entry": p.get("name_ua") or p.get("name_ru"), "questions": {},
                "pages": [{"name_ua": p.get("name_ua") or p.get("name_ru"), "name_ru": p.get("name_ru") or p.get("name_ua"),
                           "ua": p.get("ua") or [], "ru": p.get("ru") or [], "main": True}]} for p in pages]
    (outdir / "semantics.json").write_text(collect.dumps(results), encoding="utf-8")
    await asyncio.to_thread(collect.semantics_xlsx, results, outdir / "Semantics.xlsx")
    left = sum(1 for p in pages for l in ("ua", "ru") for _, v in (p.get(l) or []) if v is None)
    db.update(jid, status="done" if not left else "partial", progress=1.0, files=["Semantics.xlsx"],
              error=f"Без частотності лишилось ключів: {left}" if left else "")
    db.log(jid, "Готово. Кластери — на аркушах ua / ru, як у вихідному файлі.")


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
            runner = {"semantics": run_semantics_job, "volumes": run_volumes_job, "texts": run_texts_job}.get(kind, run_job)
            asyncio.run(runner(jid))
        except Exception as e:  # noqa: BLE001
            db.update(jid, status="error", error=str(e))
            db.log(jid, "Фатальна помилка: " + "".join(traceback.format_exception_only(type(e), e)))

    threading.Thread(target=_runner, daemon=True).start()
