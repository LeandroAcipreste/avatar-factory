import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import ALLOWED_EXTENSIONS, BASE_DIR, MAX_UPLOAD_BYTES, UPLOAD_DIR
from .database import create_job, get_job, init_db, list_jobs, update_job
from .media import MediaValidationError, extract_images, probe_video
from .quality import recommendations

app = FastAPI(title="Avatar Factory", version="0.1.0", description="MVP local para validação e preparação de vídeos consentidos.")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=BASE_DIR / "data"), name="media")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

@app.on_event("startup")
def startup() -> None:
    init_db()


def serialize(row):
    if row is None:
        return None
    item = dict(row)
    item["sample_frames"] = json.loads(item.pop("sample_frames_json") or "[]")
    item["recommendations"] = json.loads(item.pop("recommendations_json") or "[]")
    return item

@app.get("/", include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"jobs": [serialize(row) for row in list_jobs()]})

@app.post("/jobs", include_in_schema=False)
async def create_job_from_form(
    video: UploadFile = File(...),
    consent_name: str = Form(...),
    consent: str | None = Form(None),
):
    try:
        job = await save_and_process(video, consent_name, consent)
    except MediaValidationError as exc:
        return RedirectResponse(url=f"/?error={str(exc)}", status_code=303)
    return RedirectResponse(url=f"/jobs/{job['id']}", status_code=303)

@app.post("/api/jobs", status_code=201)
async def create_job_api(
    video: UploadFile = File(...),
    consent_name: str = Form(...),
    consent: str | None = Form(None),
):
    return await save_and_process(video, consent_name, consent)

async def save_and_process(video: UploadFile, consent_name: str, consent: str | None):
    if consent != "on" or not consent_name.strip():
        raise MediaValidationError("Informe seu nome e confirme o consentimento de uso de imagem.")
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise MediaValidationError("Envie apenas arquivos MP4, MOV ou WebM.")
    job_id = str(uuid.uuid4())
    stored_filename = f"{job_id}{suffix}"
    destination = UPLOAD_DIR / stored_filename
    written = 0
    job_persisted = False
    try:
        with destination.open("wb") as output:
            while chunk := await video.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise MediaValidationError(f"Arquivo excede o limite de {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
                output.write(chunk)
        job_data = {"id": job_id, "original_filename": Path(video.filename).name, "stored_filename": stored_filename,
                    "consent_name": consent_name.strip(), "created_at": datetime.now(timezone.utc).isoformat(),
                    "status": "processing", "error_message": None, "file_size": written}
        create_job(job_data)
        job_persisted = True
        info = probe_video(destination)
        notes = recommendations(info["duration"], info["width"], info["height"], written)
        try:
            thumbnail, frames = extract_images(destination, job_id, info["duration"])
        except MediaValidationError:
            thumbnail, frames = None, []
            notes.append("Não foi possível extrair imagens de amostra; a análise básica foi concluída.")
        update_job(job_id, status="ready", duration_seconds=info["duration"], width=info["width"], height=info["height"], codec=info["codec"], thumbnail_path=thumbnail, sample_frames_json=json.dumps(frames), recommendations_json=json.dumps(notes, ensure_ascii=False))
        return serialize(get_job(job_id))
    except MediaValidationError as exc:
        destination.unlink(missing_ok=True)
        if job_persisted:
            update_job(job_id, status="failed", error_message=str(exc), recommendations_json=json.dumps([str(exc)], ensure_ascii=False))
        raise
    finally:
        await video.close()

@app.get("/jobs/{job_id}", include_in_schema=False)
def job_detail(request: Request, job_id: str):
    job = serialize(get_job(job_id))
    if not job:
        raise HTTPException(404, "Job não encontrado")
    return templates.TemplateResponse(request, "job.html", {"job": job})

@app.get("/api/jobs")
def jobs_api():
    return [serialize(row) for row in list_jobs()]

@app.get("/api/jobs/{job_id}")
def job_api(job_id: str):
    job = serialize(get_job(job_id))
    if not job:
        raise HTTPException(404, "Job não encontrado")
    return job

@app.get("/health")
def health():
    return {"status": "ok", "service": "avatar-factory"}
