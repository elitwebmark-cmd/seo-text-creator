import importlib
import os
import time
from pathlib import Path

os.environ["MOCK"] = "1"
os.environ["APP_PASSWORD"] = ""
os.environ["DATA_DIR"] = str(Path(__file__).parent / ".data")

from fastapi.testclient import TestClient  # noqa: E402

from app import semantics, analysis  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_parse_text_and_pair():
    ua = semantics.parse_text("# SERM\nserm послуги; 40\nуправління репутацією в інтернеті; 30")
    ru = semantics.parse_text("# SERM\nserm услуги; 150\nуправление репутацией в интернете; 150")
    pages = semantics.auto_pair(ua, ru)
    assert pages == [{"ua": 0, "ru": 0}]


def test_phrase_count_morphology():
    t = "Послуги маркетолога. Послуг маркетологів немає. послуга маркетолога"
    assert analysis.count_phrase(t, "послуги маркетолога") == 3


def test_template_roundtrip():
    r = client.get("/api/template.xlsx")
    assert r.status_code == 200
    p = semantics.parse_xlsx(r.content)
    assert p["ua"][0]["name"] == "SERM просування" and len(p["ru"][0]["keywords"]) == 2


def test_full_job_mock():
    r = client.post("/api/parse", data={"text_ua": "# SERM\nserm послуги; 40", "text_ru": "# SERM\nserm услуги; 150"})
    assert r.status_code == 200, r.text
    d = r.json()
    body = {"project": {"domain": "elit-web.ua"}, "formats": ["advanced", "base"],
            "pages": [{"name_ua": "SERM", "name_ru": "SERM", "ua": d["ua"][0]["keywords"], "ru": d["ru"][0]["keywords"]}]}
    jid = client.post("/api/jobs", json=body).json()["id"]
    for _ in range(60):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "error", "partial"):
            break
        time.sleep(0.5)
    assert j["status"] == "done", j
    assert len(j["files"]) == 2
    for f in j["files"]:
        assert client.get(f"/api/jobs/{jid}/files/{f}").status_code == 200


def test_semantics_job_mock():
    jid = client.post("/api/semantics", json={"entries": ["SERM послуги", "https://example.com/ua/serm"], "langs": ["ua", "ru"]}).json()["id"]
    for _ in range(60):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "error", "partial"):
            break
        time.sleep(0.3)
    assert j["status"] == "done", j
    d = client.get(f"/api/jobs/{jid}/semantics").json()
    assert d["ua"] and d["ru"] and d["pages"]
    assert client.get(f"/api/jobs/{jid}/files/Semantics.xlsx").status_code == 200
    kinds = {x["id"]: x["kind"] for x in client.get("/api/jobs").json()}
    assert kinds[jid] == "semantics"
