"""Тексти у Word: кожна сторінка × мова — окремий розділ з нової сторінки + звіт перевірки."""
import re

from docx import Document
from docx.enum.text import WD_BREAK
from docx.shared import Pt, RGBColor


def _runs(par, text):
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            r = par.add_run(part[2:-2]); r.bold = True
        else:
            par.add_run(part)


def _md(doc, md):
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        m = re.match(r"^(#{1,3})\s+(.+)$", ln)
        if m:
            doc.add_heading(m.group(2).strip(), level=len(m.group(1)))
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\|?\s*:?-{2,}", lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            i -= 1
            if rows:
                cols = max(len(r) for r in rows)
                t = doc.add_table(rows=len(rows), cols=cols)
                t.style = "Table Grid"
                for ri, r in enumerate(rows):
                    for ci, v in enumerate(r):
                        cell = t.cell(ri, ci)
                        cell.text = v.replace("**", "")
                        if ri == 0:
                            for p in cell.paragraphs:
                                for run in p.runs:
                                    run.bold = True
        elif re.match(r"^\s*[-*•]\s+", ln):
            _runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*•]\s+", "", ln))
        elif re.match(r"^\s*\d+[.)]\s+", ln):
            _runs(doc.add_paragraph(style="List Number"), re.sub(r"^\s*\d+[.)]\s+", "", ln))
        elif ln.strip():
            _runs(doc.add_paragraph(), ln.strip())
        i += 1


def build(texts: list[dict], path):
    """texts: [{title, lang, url, title_tag, desc, markdown, report, spelling}]"""
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Arial"
    st.font.size = Pt(11)
    doc.add_heading("SEO-тексти", 0)
    p = doc.add_paragraph("Зміст: ")
    for t in texts:
        doc.add_paragraph(f"{t['title']} — {t['lang'].upper()} ({t['report']['chars']} зн.)", style="List Bullet")
    for t in texts:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        meta = doc.add_paragraph()
        r = meta.add_run(f"{t['title']} · {t['lang'].upper()} · {t.get('url') or ''}")
        r.italic = True
        r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        if t.get("title_tag"):
            mp = doc.add_paragraph(); mp.add_run("Title: ").bold = True; mp.add_run(t["title_tag"])
        if t.get("desc"):
            mp = doc.add_paragraph(); mp.add_run("Description: ").bold = True; mp.add_run(t["desc"])
        _md(doc, t["markdown"])
        rep = t["report"]
        doc.add_heading("Перевірка тексту (службовий блок, не публікувати)", 3)
        doc.add_paragraph(f"Обсяг: {rep['chars']} зн. з пробілами. Раундів редагування: {t.get('rounds', 0)}.")
        if rep["issues"]:
            doc.add_paragraph("Не вдалося виправити автоматично:")
            for x in rep["issues"]:
                doc.add_paragraph(x, style="List Bullet")
        for x in rep.get("warnings", []):
            doc.add_paragraph(x, style="List Bullet")
        if t.get("spelling"):
            doc.add_paragraph("Залишкові зауваження LanguageTool (перевірте вручну, частина може бути хибною):")
            for x in t["spelling"][:15]:
                doc.add_paragraph(x, style="List Bullet")
        if rep.get("placeholders"):
            doc.add_paragraph("Плейсхолдери для заповнення: " + "; ".join(dict.fromkeys(rep["placeholders"])))
        if rep.get("keywords"):
            tbl = doc.add_table(rows=1, cols=4)
            tbl.style = "Table Grid"
            for i, h in enumerate(["Тип", "Ключ / LSI", "Входжень", "Норма"]):
                tbl.rows[0].cells[i].text = h
            for kind, k, n, f in rep["keywords"]:
                row = tbl.add_row().cells
                row[0].text, row[1].text, row[2].text, row[3].text = kind, k, str(n), str(f)
    doc.save(path)
