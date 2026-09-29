import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import ALLOWED_EXTENSIONS, BASE_DIR, DATA_DIR, DERIVED_DIR, MAX_UPLOAD_BYTES, UPLOAD_DIR
from .audio import analyze_audio
from .database import create_job, get_job, init_db, list_jobs, update_job
from .gpu_dispatch import KaggleGpuDispatcher, dispatch_history_entry
from .media import MediaValidationError, extract_images, probe_video
from .quality import recommendations

app = FastAPI(title="Avatar Factory", version="0.1.0", description="MVP local para validação e preparação de vídeos consentidos.")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

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
    item["audio_report"] = json.loads(item.pop("audio_report_json", None) or "{}")
    item["dispatch_history"] = json.loads(item.pop("dispatch_history_json", None) or "[]")
    item["gpu_result"] = json.loads(item.pop("gpu_result_json", None) or "{}")
    item["gpu_refs"] = json.loads(item.pop("gpu_refs_json", None) or "{}")
    item["wav_available"] = safe_wav_path(item) is not None
    return item


def safe_wav_path(job):
    """Constrain worker output to this job's derived/output directories."""
    job_id = str(job["id"])
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", job_id):
        return None
    result = job.get("gpu_result") or {}
    raw = result.get("wav_path") or result.get("audio_path")
    if not isinstance(raw, str) or not raw:
        return None
    for root in (DERIVED_DIR.resolve(), (DATA_DIR / "outputs").resolve()):
        folder = (root / job_id).resolve()
        if not folder.is_relative_to(root):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = DATA_DIR / candidate if candidate.parts[0] in {"derived", "outputs"} else folder / candidate
        candidate = candidate.resolve()
        if candidate.is_relative_to(folder) and candidate.suffix.lower() == ".wav" and candidate.is_file():
            return candidate
    return None


def apply_gpu_result(job_id, result):
    """Accept status/provider/message plus optional refs/result dictionaries."""
    def field(name, default=None):
        return result.get(name, default) if isinstance(result, dict) else getattr(result, name, default)
    changes = {}
    for name, column in (("refs", "gpu_refs_json"), ("result", "gpu_result_json")):
        value = field(name)
        if isinstance(value, dict):
            changes[column] = json.dumps(value, ensure_ascii=False)
    refs = {key: field(key) for key in ("kernel_ref", "dataset_ref") if field(key)}
    if refs:
        changes["gpu_refs_json"] = json.dumps(refs, ensure_ascii=False)
    manifest = field("manifest")
    if isinstance(manifest, dict) or field("output_path"):
        payload = dict(manifest or {})
        if field("output_path"):
            payload["wav_path"] = field("output_path")
        changes["gpu_result_json"] = json.dumps(payload, ensure_ascii=False)
    status = field("status")
    if status:
        changes["status"] = status
        changes["gpu_error_message"] = field("message") if status in {"gpu_dispatch_failed", "gpu_failed", "gpu_collection_failed", "gpu_collect_failed"} else None
    if field("provider"):
        changes["gpu_provider"] = field("provider")
    update_job(job_id, **changes)


def refreshed_job(job_id):
    job = serialize(get_job(job_id))
    if not job:
        raise HTTPException(404, "Job não encontrado")
    if job["status"] in {"gpu_queued", "gpu_running", "gpu_collecting", "gpu_collect_failed"}:
        collect = getattr(KaggleGpuDispatcher(), "collect", None)
        if callable(collect):
            try:
                result = collect(job)
                if result is not None:
                    apply_gpu_result(job_id, result)
            except Exception:
                update_job(job_id, gpu_error_message="Não foi possível consultar o resultado GPU; tente consultar novamente.")
            job = serialize(get_job(job_id))
    return job

@app.get("/", include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"jobs": [serialize(row) for row in list_jobs()]})

@app.post("/jobs", include_in_schema=False)
async def create_job_from_form(
    video: UploadFile = File(...),
    consent_name: str = Form(...),
    consent: str | None = Form(None),
    speech_text: str = Form(""),
):
    try:
        job = await save_and_process(video, consent_name, consent, speech_text)
    except MediaValidationError as exc:
        return RedirectResponse(url=f"/?error={str(exc)}", status_code=303)
    return RedirectResponse(url=f"/jobs/{job['id']}", status_code=303)

@app.post("/api/jobs", status_code=201)
async def create_job_api(
    video: UploadFile = File(...),
    consent_name: str = Form(...),
    consent: str | None = Form(None),
    speech_text: str = Form(""),
):
    try:
        return await save_and_process(video, consent_name, consent, speech_text)
    except MediaValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

async def save_and_process(video: UploadFile, consent_name: str, consent: str | None, speech_text: str = ""):
    speech_text = speech_text.strip()
    if len(speech_text) > 1000:
        raise MediaValidationError("O texto da fala deve ter no máximo 1000 caracteres.")
    if consent != "on" or not consent_name.strip():
        raise MediaValidationError("Informe seu nome e confirme a declaração de consentimento para imagem e voz.")
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
                    "status": "uploaded", "processing_status": "processing", "error_message": None, "file_size": written,
                    "gpu_provider": "kaggle", "gpu_error_message": None, "dispatch_history_json": "[]",
                    "speech_text": speech_text, "gpu_result_json": "{}", "gpu_refs_json": "{}",
                    "consent_declaration": "Autorizo o uso da imagem e voz desta pessoa para criar avatar e conteúdos autorizados pelo cliente neste fluxo local."}
        create_job(job_data)
        job_persisted = True
        # Persist first so every forwarding attempt is auditable, then dispatch
        # automatically.  The dispatcher is disabled by default and never asks
        # for a second consent declaration.
        dispatch_input = {**job_data, "upload_path": str(destination), "synthesis_text": speech_text}
        dispatch_result = KaggleGpuDispatcher().dispatch(dispatch_input)
        apply_gpu_result(job_id, dispatch_result)
        history = [dispatch_history_entry(dispatch_result)]
        update_job(
            job_id,
            status=dispatch_result.status,
            gpu_provider=dispatch_result.provider,
            gpu_error_message=dispatch_result.message if dispatch_result.status == "gpu_dispatch_failed" else None,
            dispatch_history_json=json.dumps(history, ensure_ascii=False),
        )
        info = probe_video(destination)
        notes = recommendations(info["duration"], info["width"], info["height"], written)
        audio_report = analyze_audio(destination)
        try:
            thumbnail, frames = extract_images(destination, job_id, info["duration"])
        except MediaValidationError:
            thumbnail, frames = None, []
            notes.append("Não foi possível extrair imagens de amostra; a análise básica foi concluída.")
        visual_passes = info["duration"] >= 10 and min(info["width"], info["height"]) >= 720
        audio_passes = audio_report["status"] == "detected" and audio_report["quality"] == "adequada"
        processing_status = "avatar_prepared" if visual_passes and audio_passes else "ready"
        update_job(job_id, processing_status=processing_status, duration_seconds=info["duration"], width=info["width"], height=info["height"], codec=info["codec"], thumbnail_path=thumbnail, sample_frames_json=json.dumps(frames), recommendations_json=json.dumps(notes, ensure_ascii=False), audio_report_json=json.dumps(audio_report, ensure_ascii=False))
        return serialize(get_job(job_id))
    except MediaValidationError as exc:
        destination.unlink(missing_ok=True)
        if job_persisted:
            update_job(job_id, processing_status="failed", error_message=str(exc), recommendations_json=json.dumps([str(exc)], ensure_ascii=False))
        raise
    finally:
        await video.close()

@app.get("/jobs/{job_id}", include_in_schema=False)
def job_detail(request: Request, job_id: str):
    job = refreshed_job(job_id)
    return templates.TemplateResponse(request, "job.html", {"job": job})

@app.get("/api/jobs")
def jobs_api():
    return [serialize(row) for row in list_jobs()]

@app.get("/api/jobs/{job_id}")
def job_api(job_id: str):
    return refreshed_job(job_id)


@app.get("/media/derived/{job_id}/{filename}", include_in_schema=False)
def derived_image(job_id: str, filename: str):
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", job_id) or not re.fullmatch(r"thumbnail\.jpg|sample-[1-3]\.jpg", filename):
        raise HTTPException(404, "Mídia não encontrada")
    root = DERIVED_DIR.resolve()
    folder = (root / job_id).resolve()
    path = (folder / filename).resolve()
    if not folder.is_relative_to(root) or not path.is_relative_to(folder) or not path.is_file() or not get_job(job_id):
        raise HTTPException(404, "Mídia não encontrada")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/jobs/{job_id}/audio.wav", include_in_schema=False)
def download_audio(job_id: str):
    job = serialize(get_job(job_id))
    path = safe_wav_path(job) if job else None
    if path is None:
        raise HTTPException(404, "Áudio indisponível para este job")
    return FileResponse(path, media_type="audio/wav", filename=f"{job_id}.wav")

@app.get("/health")
def health():
    return {"status": "ok", "service": "avatar-factory"}
