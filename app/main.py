import io
from pathlib import Path

import openpyxl
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel, Field

from . import config, db, pipeline, semantics

BASE = Path(__file__).parent
app = FastAPI(title="SEO ТЗ генератор")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
tpl = Jinja2Templates(directory=BASE / "templates")
signer = URLSafeSerializer(config.SECRET_KEY, salt="auth")
COOKIE = "tz_auth"
db.init()


def authed(request: Request) -> bool:
    if not config.APP_PASSWORD:
        return True
    tok = request.cookies.get(COOKIE)
    if not tok:
        return False
    try:
        return signer.loads(tok) == "ok"
    except BadSignature:
        return False


# версія статики для скидання кешу браузера після кожного деплою
import hashlib as _h
ASSET_V = _h.md5(b"".join((BASE / "static" / f).read_bytes() for f in ("app.js", "style.css"))).hexdigest()[:8]
tpl.env.globals["v"] = ASSET_V


@app.middleware("http")
async def guard(request: Request, call_next):
    open_paths = ("/login", "/static", "/api/health")
    if not request.url.path.startswith(open_paths) and not authed(request):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return RedirectResponse("/login")
    resp = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    return tpl.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
def login(password: str = Form(...)):
    if password != config.APP_PASSWORD:
        return RedirectResponse("/login?error=1", status_code=303)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, signer.dumps("ok"), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return r


@app.get("/logout")
def logout():
    r = RedirectResponse("/login")
    r.delete_cookie(COOKIE)
    return r


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return tpl.TemplateResponse(request, "index.html", {"mock": config.MOCK, "auth": bool(config.APP_PASSWORD)})


@app.get("/api/diag")
async def diag():
    from . import volumes
    return {"dataforseo": await volumes.diagnose()}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mock": config.MOCK,
        "anthropic_key": bool(config.ANTHROPIC_API_KEY),
        "serper_key": bool(config.SERPER_API_KEY),
        "model": config.ANTHROPIC_MODEL,
        "dataforseo": bool(config.DATAFORSEO_LOGIN and config.DATAFORSEO_PASSWORD),
    }


def _cl(c):
    return {"name": c["name"], "keywords": [[k, v] for k, v in c["keywords"]]}


@app.post("/api/parse")
async def parse(file: UploadFile | None = File(None), text_ua: str = Form(""), text_ru: str = Form("")):
    ua, ru = [], []
    if file is not None and file.filename:
        data = await file.read()
        try:
            parsed = semantics.parse_xlsx(data)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Не вдалося прочитати xlsx: {e}")
        ua, ru = parsed["ua"], parsed["ru"]
    if text_ua.strip():
        ua += semantics.parse_text(text_ua)
    if text_ru.strip():
        ru += semantics.parse_text(text_ru)
    if not ua and not ru:
        raise HTTPException(400, "Семантику не знайдено. Перевірте формат файлу або тексту.")
    return {"ua": [_cl(c) for c in ua], "ru": [_cl(c) for c in ru], "pages": semantics.auto_pair(ua, ru)}


class PageIn(BaseModel):
    name_ua: str = ""
    name_ru: str = ""
    ua: list[list] = Field(default_factory=list)
    ru: list[list] = Field(default_factory=list)
    notes: str = ""


class JobIn(BaseModel):
    title: str = ""
    project: dict = Field(default_factory=dict)
    pages: list[PageIn]
    formats: list[str] = Field(default_factory=lambda: ["advanced"])


@app.post("/api/jobs")
def create_job(body: JobIn):
    pages = []
    for p in body.pages:
        d = p.model_dump()
        for l in ("ua", "ru"):
            d[l] = [(str(k), semantics._vol(v)) for k, v in d[l] if str(k).strip()]
            d[l].sort(key=lambda x: -(x[1] or 0))
        if d["ua"] or d["ru"]:
            pages.append(d)
    if not pages:
        raise HTTPException(400, "Немає жодної сторінки з ключами")
    formats = [f for f in body.formats if f in ("advanced", "base")] or ["advanced"]
    if not config.MOCK:
        missing = [n for n, v in (("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY), ("SERPER_API_KEY", config.SERPER_API_KEY)) if not v]
        if missing:
            raise HTTPException(400, "На сервері не задано: " + ", ".join(missing))
    title = body.title or f"{body.project.get('domain') or 'Проєкт'} — {len(pages)} стор."
    jid = db.create(title, {"project": body.project, "pages": pages, "formats": formats})
    pipeline.start(jid)
    return {"id": jid}


class SemIn(BaseModel):
    entries: list[str]
    langs: list[str] = Field(default_factory=lambda: ["ua", "ru"])
    project: dict = Field(default_factory=dict)


@app.post("/api/semantics")
def collect_semantics(body: SemIn):
    entries = [e.strip() for e in body.entries if e.strip()][:20]
    langs = [l for l in body.langs if l in ("ua", "ru")] or ["ua"]
    if not entries:
        raise HTTPException(400, "Вкажіть хоча б одну послугу або URL")
    if not config.MOCK:
        missing = [n for n, v in (("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY), ("SERPER_API_KEY", config.SERPER_API_KEY)) if not v]
        if missing:
            raise HTTPException(400, "На сервері не задано: " + ", ".join(missing))
    title = "Семантика: " + ", ".join(e[:40] for e in entries[:3]) + ("…" if len(entries) > 3 else "")
    jid = db.create(title, {"kind": "semantics", "entries": entries, "langs": langs, "project": body.project})
    pipeline.start(jid)
    return {"id": jid}


@app.get("/api/jobs/{jid}/semantics")
def job_semantics(jid: str):
    from . import collect
    import json as _json
    path = config.DATA_DIR / "jobs" / jid / "semantics.json"
    if not path.exists():
        raise HTTPException(404, "Семантика ще не готова")
    results = _json.loads(path.read_text(encoding="utf-8"))
    out = collect.to_semantics(results)
    out["notes"] = [{"entry": r["entry"], "notes": r.get("notes", ""), "source": r.get("volumes_source", "")} for r in results]
    return out


@app.get("/api/jobs")
def jobs():
    return db.list_jobs()


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = db.get(jid)
    if not j:
        raise HTTPException(404)
    j.pop("params", None)
    return j


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    db.delete(jid)
    return {"ok": True}


@app.get("/api/jobs/{jid}/files/{name}")
def download(jid: str, name: str):
    j = db.get(jid)
    if not j or name not in (j.get("files") or []):
        raise HTTPException(404)
    path = config.DATA_DIR / "jobs" / jid / name
    return FileResponse(path, filename=name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/template.xlsx")
def template():
    wb = openpyxl.Workbook()
    for i, (lang, rows) in enumerate((
        ("ua", [("SERM просування", None, None), (None, "serm послуги", 40), (None, "управління репутацією в інтернеті", 30)]),
        ("ru", [("SERM продвижение", None, None), (None, "serm услуги", 150), (None, "управление репутацией в интернете", 150)]),
    )):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = lang
        ws.append(["кластер", "Ключове слово", "Частотність"])
        for r in rows:
            ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=semantics_template.xlsx"})
