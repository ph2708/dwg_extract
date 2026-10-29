import os
import shutil
import tempfile
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cad_to_ai import extract_project_summary, format_ai_prompt

app = FastAPI(title="Extrator Universal CAD para IA", version="1.0.0")

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
async def api_extract(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".dwg", ".dxf")):
        return JSONResponse(
            status_code=400,
            content={"error": "Formato inválido. Por favor envie arquivos .dwg ou .dxf."}
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = Path(tmpdir) / file.filename
        with open(tmp_file, "wb") as f:
            shutil.copyfileobj(file.file, f)

        try:
            summary = extract_project_summary(str(tmp_file))
            prompt_text = format_ai_prompt(summary)
            return {
                "success": True,
                "filename": file.filename,
                "summary": summary,
                "prompt_text": prompt_text
            }
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"error": f"Erro ao processar projeto CAD: {str(e)}"}
            )

@app.get("/health")
async def health():
    return {"status": "ok", "app": "Extrator DWG AI"}
