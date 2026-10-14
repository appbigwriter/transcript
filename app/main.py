import asyncio
import base64
import contextlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .core import LANGUAGES, PROVIDERS, Settings, publication_url, subtitles
from .store import JobStore
from .sessions import SessionStore, PLATFORMS, platform_id

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"

def kill_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)

def worker_loop(store, settings, stop):
    work = settings.data_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        store.cleanup(settings.retention)
        job = store.next()
        if not job:
            stop.wait(1)
            continue
        store.update(job["id"], status="running", stage="Iniciando", progress=1)
        with tempfile.TemporaryDirectory(prefix="job-", dir=work) as temporary:
            directory = Path(temporary)
            if job.get('kind') == 'upload':
                source = settings.data_dir / 'uploads' / (job['upload_id'] + '.media')
                if source.is_file():
                    source.replace(directory / 'upload.media')
            (directory / "job.json").write_text(json.dumps(job), encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            environment["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen([sys.executable, "-m", "app.pipeline"], cwd=directory, env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                       start_new_session=os.name != "nt", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            def watchdog():
                deadline = time.monotonic() + settings.timeout
                while process.poll() is None:
                    if stop.wait(0.5) or time.monotonic() > deadline:
                        kill_tree(process)
                        return
            watcher = threading.Thread(target=watchdog, daemon=True)
            watcher.start()
            for line in process.stdout:
                try:
                    update = json.loads(line)
                    if "stage" in update and "progress" in update:
                        store.update(job["id"], stage=update["stage"], progress=update["progress"])
                except (ValueError, TypeError):
                    continue
            process.stdout.close()
            process.wait()
            watcher.join(timeout=2)
            result_file = directory / "result.json"
            if result_file.is_file():
                try:
                    result = json.loads(result_file.read_text(encoding="utf-8"))
                except ValueError:
                    result = {"error": "O processamento retornou um resultado inválido."}
            else:
                result = {"error": "A tarefa foi interrompida ou excedeu o tempo máximo. Partes já enviadas à API podem ter sido cobradas."}
            if result.get("error"):
                store.update(job["id"], status="failed", stage="Não concluído", error=result["error"], error_code=result.get('error_code', 'processing_error'))
            else:
                store.update(job["id"], status="done", stage="Transcrição pronta", progress=100, result=result["result"])

@asynccontextmanager
async def lifespan(app):
    settings = Settings.load()
    if not settings.allow_public and len(settings.password) < 12:
        raise RuntimeError("Defina FBR_PASSWORD com pelo menos 12 caracteres no Easypanel. Para acesso público deliberado, use FBR_ALLOW_PUBLIC=1.")
    if settings.cookies_file and not Path(settings.cookies_file).is_absolute():
        raise RuntimeError("FBR_COOKIES_FILE deve ser um caminho absoluto.")
    # Child processes change directory; paths must remain stable across that change.
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FBR_DATA_DIR"] = str(settings.data_dir.resolve())
    settings = Settings.load()
    store = JobStore(settings.data_dir)
    store.initialize()
    uploads = settings.data_dir / 'uploads'
    if uploads.exists():
        keep = store.queued_uploads()
        for file in uploads.glob('*.media'):
            if re.fullmatch(r'[a-f0-9]{32}', file.stem) and file.stem not in keep:
                file.unlink(missing_ok=True)
    work = settings.data_dir / "work"
    if work.exists():
        for child in work.glob("job-*"):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
    app.state.settings, app.state.store = settings, store
    app.state.sessions = SessionStore(settings)
    app.state.submissions = deque()
    app.state.submission_lock = threading.Lock()
    stop = threading.Event()
    thread = threading.Thread(target=worker_loop, args=(store, settings, stop), daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        await asyncio.to_thread(thread.join, 10)

app = FastAPI(title="FBR-Transcript", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def protection(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    settings = request.app.state.settings
    if not settings.allow_public:
        try:
            scheme, credentials = request.headers.get("authorization", "").split(" ", 1)
            user, password = base64.b64decode(credentials, validate=True).decode().split(":", 1)
            valid = scheme.lower() == "basic" and secrets.compare_digest(user.encode(), settings.username.encode()) and secrets.compare_digest(password.encode(), settings.password.encode())
        except (ValueError, UnicodeDecodeError):
            valid = False
        if not valid:
            return Response("Autenticação necessária.", status_code=401, headers={"WWW-Authenticate": 'Basic realm="FBR-Transcript", charset="UTF-8"'})
    # Same-origin mutating requests; no CORS surface for third-party pages.
    if request.method in {"POST", "DELETE", "PUT"}:
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
        try:
            limit = settings.max_download if request.url.path == '/api/jobs/upload' else 1100000 if request.url.path.startswith('/api/sessions/') else 8192
            if int(request.headers.get("content-length", "0")) > limit:
                return JSONResponse({"detail": "Requisição muito grande."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Requisição inválida."}, status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/config")
def configuration(request: Request):
    settings = request.app.state.settings
    from urllib.parse import urlsplit
    browser_url = os.getenv('FBR_BROWSER_URL', '').strip() if not settings.allow_public else ''
    parsed = urlsplit(browser_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        browser_url = ''
    return {"provider": settings.provider, "model": settings.model, "ready": bool(settings.key), "timestamps": settings.timestamps, "max_minutes": settings.max_duration // 60, "retention_hours": settings.retention // 3600, 'max_upload_mb': settings.max_download // (1024 * 1024), 'session_management': not settings.allow_public, 'browser_sync': bool(os.getenv('FBR_BROWSER_PROFILE')) and not settings.allow_public, 'browser_url': browser_url}

def private_sessions(request):
    if request.app.state.settings.allow_public:
        raise HTTPException(403, 'O gerenciamento de sessões exige que o aplicativo esteja protegido por senha.')
    return request.app.state.sessions

@app.get('/api/sessions')
def list_sessions(request: Request):
    sessions = private_sessions(request)
    return {'sessions': [sessions.describe(platform) for platform in PLATFORMS]}

class SessionImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=1048576)

@app.put('/api/sessions/{platform}')
def save_session(platform: str, body: SessionImport, request: Request):
    sessions = private_sessions(request)
    try:
        return sessions.save(platform, body.content)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None

@app.delete('/api/sessions/{platform}')
def remove_session(platform: str, request: Request):
    try:
        private_sessions(request).remove(platform)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return {'removed': True, 'notice': 'Sessão removida deste aplicativo. Para revogá-la na plataforma, encerre a sessão nas configurações da conta.'}

@app.post('/api/sessions/{platform}/sync')
def sync_browser_session(platform: str, request: Request):
    try:
        return private_sessions(request).sync_firefox(platform)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None

class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    language: Literal["auto", "pt", "en", "es", "fr", "de", "it"] = "auto"

@app.post("/api/jobs", status_code=202)
def create_job(body: TranscriptionRequest, request: Request):
    settings, store = request.app.state.settings, request.app.state.store
    if not settings.key:
        raise HTTPException(503, f"Configure {PROVIDERS[settings.provider]['key']} no Easypanel para começar.")
    try:
        url, platform = publication_url(body.url)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return enqueue(request, {"url": url, "platform": platform, "language": body.language})

def enqueue(request, data):
    settings, store = request.app.state.settings, request.app.state.store
    with request.app.state.submission_lock:
        now = time.monotonic()
        submissions = request.app.state.submissions
        # Global hourly cap across the shared access account. One process/replica by design.
        while submissions and submissions[0] < now - 3600:
            submissions.popleft()
        if len(submissions) >= 30:
            raise HTTPException(429, "Limite de 30 novas tarefas por hora atingido. Aguarde para continuar.")
        try:
            result = store.create(data, settings.max_queue)
        except ValueError as error:
            raise HTTPException(429, str(error)) from None
        submissions.append(now)
    return result

@app.post('/api/sessions/{platform}/check', status_code=202)
def check_session(platform: str, body: TranscriptionRequest, request: Request):
    sessions = private_sessions(request)
    if platform not in PLATFORMS or not sessions.describe(platform)['configured']:
        raise HTTPException(400, 'Importe os cookies desta plataforma primeiro.')
    try:
        url, label = publication_url(body.url)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    if platform_id(label) != platform:
        raise HTTPException(400, 'O link precisa pertencer à plataforma selecionada.')
    return enqueue(request, {'kind': 'session_check', 'url': url, 'platform': label, 'language': 'auto'})

@app.post('/api/jobs/upload', status_code=202)
async def upload_job(request: Request, language: str = 'auto'):
    from urllib.parse import unquote
    settings = request.app.state.settings
    if not settings.key:
        raise HTTPException(503, 'Configure a chave da API no servidor antes de transcrever.')
    if language not in LANGUAGES:
        raise HTTPException(400, 'Idioma inválido.')
    name = unquote(request.headers.get('x-file-name', 'arquivo'))
    name = name.replace('\\', '/').split('/')[-1][:200]
    if Path(name).suffix.lower() not in {'.mp3', '.mp4', '.m4a', '.wav', '.webm', '.mov', '.mkv', '.ogg', '.flac', '.aac'}:
        raise HTTPException(400, 'Formato de áudio ou vídeo não aceito.')
    identifier = secrets.token_hex(16)
    directory = settings.data_dir / 'uploads'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (identifier + '.media')
    accepted = False
    try:
        total = 0
        with path.open('xb') as stream:
            os.chmod(path, 0o600)
            async for chunk in request.stream():
                total += len(chunk)
                if total > settings.max_download:
                    raise HTTPException(413, 'O arquivo excede o limite de tamanho.')
                stream.write(chunk)
        if total == 0:
            raise HTTPException(400, 'O arquivo está vazio.')
        result = enqueue(request, {'kind': 'upload', 'url': None, 'platform': 'Arquivo enviado', 'language': language, 'upload_id': identifier, 'upload_name': name})
        accepted = True
        return result
    finally:
        if not accepted:
            path.unlink(missing_ok=True)

def job_or_404(request, identifier):
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise HTTPException(404, "Transcrição não encontrada.")
    job = request.app.state.store.get(identifier)
    if not job:
        raise HTTPException(404, "Transcrição não encontrada ou expirada.")
    if job["status"] in {"done", "failed"} and job.get("updated", job["created"]) < time.time() - request.app.state.settings.retention:
        # Cleanup runs every second; refuse stale records even before the next sweep.
        raise HTTPException(404, "Transcrição expirada.")
    return job

@app.get("/api/jobs/{identifier}")
def get_job(identifier: str, request: Request):
    return job_or_404(request, identifier)

@app.get("/api/jobs/{identifier}/download/{extension}")
def download(identifier: str, extension: str, request: Request):
    job = job_or_404(request, identifier)
    if job["status"] != "done":
        raise HTTPException(409, "A transcrição ainda não está pronta.")
    result = job["result"]
    if extension == "txt":
        content, mime = result["text"], "text/plain"
    elif extension in {"srt", "vtt"} and result.get("segments"):
        content, mime = subtitles(result["segments"], extension == "vtt"), "text/vtt" if extension == "vtt" else "application/x-subrip"
    else:
        raise HTTPException(400, "Formato indisponível. Legendas exigem tempos retornados pelo modelo.")
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="fbr-transcript-{identifier[:8]}.{extension}"'})

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")

app.mount("/static", StaticFiles(directory=STATIC), name="static")
