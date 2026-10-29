import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cad_to_ai import extract_project_summary, format_ai_prompt, format_topology_text
from auditor_bom import parse_bom_file, cross_check_cad_and_bom, generate_budget_excel

app = FastAPI(title="Extrator e Auditor Universal CAD", version="2.0.0")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/api/extract")
async def api_extract(
    file: UploadFile = File(...),
    bom_file: Optional[UploadFile] = File(None),
    unit_override: Optional[str] = Form(None)
):
    """
    Processa o projeto CAD com averiguação de escala e cruza com a Lista de Materiais (BOM).
    """
    if not file.filename.lower().endswith((".dwg", ".dxf")):
        return JSONResponse(
            status_code=400,
            content={"error": "Formato inválido. Por favor envie arquivos .dwg ou .dxf."}
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_cad = Path(tmpdir) / file.filename
        with open(tmp_cad, "wb") as f:
            shutil.copyfileobj(file.file, f)

        try:
            summary = extract_project_summary(str(tmp_cad), user_scale_override=unit_override)
            prompt_text = format_ai_prompt(summary)
            topology_text = format_topology_text(summary)

            audit_results = []
            bom_items_count = 0

            # Se foi enviada a lista de materiais do projetista
            if bom_file and bom_file.filename:
                bom_bytes = await bom_file.read()
                bom_items = parse_bom_file(bom_bytes, bom_file.filename)
                bom_items_count = len(bom_items)
                if bom_items:
                    audit_results = cross_check_cad_and_bom(summary, bom_items)

            return {
                "success": True,
                "filename": file.filename,
                "summary": summary,
                "prompt_text": prompt_text,
                "topology_text": topology_text,
                "audit_results": audit_results,
                "bom_items_count": bom_items_count
            }
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"error": f"Erro ao processar projeto CAD: {str(e)}"}
            )

@app.post("/api/export-excel")
async def api_export_excel(
    file: UploadFile = File(...),
    bom_file: Optional[UploadFile] = File(None),
    unit_override: Optional[str] = Form(None)
):
    """
    Gera e faz o download direto da planilha Excel (.xlsx) profissional com fórmulas e abas de cotação.
    """
    if not file.filename.lower().endswith((".dwg", ".dxf")):
        return JSONResponse(status_code=400, content={"error": "Envie um arquivo .dwg ou .dxf."})

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_cad = Path(tmpdir) / file.filename
        with open(tmp_cad, "wb") as f:
            shutil.copyfileobj(file.file, f)

        try:
            summary = extract_project_summary(str(tmp_cad), user_scale_override=unit_override)
            audit_results = None

            if bom_file and bom_file.filename:
                bom_bytes = await bom_file.read()
                bom_items = parse_bom_file(bom_bytes, bom_file.filename)
                if bom_items:
                    audit_results = cross_check_cad_and_bom(summary, bom_items)

            excel_bytes = generate_budget_excel(summary, audit_results)
            output_name = Path(file.filename).stem + "_Orcamento_Auditado.xlsx"

            return Response(
                content=excel_bytes,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{output_name}"'}
            )
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Erro ao gerar Excel: {str(e)}"})

@app.get("/health")
async def health():
    return {"status": "ok", "app": "Extrator e Auditor DWG"}
