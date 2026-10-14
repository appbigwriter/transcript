"""Run in a disposable process; secrets stay in server environment."""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import httpx

from .core import PROVIDERS, Settings, normalize_segments, publication_url

CHUNK_SECONDS = 600

class UserError(Exception):
    pass

def progress(stage, percent):
    print(json.dumps({"stage": stage, "progress": percent}), flush=True)

def command(arguments, timeout=300):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UserError("A plataforma demorou a responder. Tente novamente mais tarde.") from None
    if result.returncode:
        # Downloader stderr can contain sensitive URLs/session information. Do not return it.
        message = result.stderr.lower()
        if "login" in message or "sign in" in message or "cookies" in message or "private" in message:
            raise UserError("A plataforma exige login ou bloqueou o acesso. O administrador pode configurar uma sessão autorizada por cookies. Stories expirados e conteúdo sem permissão não podem ser extraídos.")
        if "ffmpeg" in message or "ffprobe" in message:
            raise UserError("Não foi possível preparar o áudio. Verifique se FFmpeg e FFprobe estão instalados no servidor.")
        raise UserError("Não foi possível acessar o áudio desta publicação. Verifique o link, a disponibilidade e as permissões. Bloqueios da plataforma ou da rede da VPS também podem impedir a extração.")
    return result.stdout

def downloader_arguments(settings):
    args = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config",
        "--no-plugin-dirs",
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "--playlist-items", "1",
        "--socket-timeout", "20",
        "--retries", "1",
        "--fragment-retries", "1",
        "--js-runtimes", "node",
        "--use-extractors", "youtube.*,instagram.*,tiktok.*,facebook.*,twitter.*",
        "--extractor-args", "youtube:player_client=android,ios,web",
    ]
    if settings.cookies_file:
        source = Path(settings.cookies_file)
        if not source.is_file():
            raise UserError("O arquivo de cookies configurado no servidor não foi encontrado.")
        # yt-dlp writes updated cookies; work with a disposable copy of the mounted session.
        import shutil
        destination = Path("session.txt")
        shutil.copyfile(source, destination)
        args += ["--cookies", str(destination.resolve())]
    return args

def duration_of(file):
    raw = command(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(file)])
    try:
        duration = float(json.loads(raw)["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
        return duration
    except (KeyError, ValueError):
        raise UserError("Não foi possível determinar a duração do áudio.") from None

def prepare_chunks(file, settings, directory):
    duration = duration_of(file)
    if duration > settings.max_duration + 1:
        raise UserError(f"O vídeo excede o limite de {settings.max_duration // 60} minutos.")
    pattern = directory / "chunk-%03d.mp3"
    command(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(file), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-reset_timestamps", "1", str(pattern)])
    files = sorted(directory.glob("chunk-*.mp3"))
    if not files or any(file.stat().st_size > 24 * 1024 * 1024 for file in files):
        raise UserError("Não foi possível dividir o áudio em partes compatíveis com a API.")
    return files, duration

def request_transcription(file, settings, language, client):
    data = {"model": settings.model, "response_format": "verbose_json" if settings.timestamps else "json"}
    if settings.timestamps:
        data["timestamp_granularities[]"] = "segment"
    if language != "auto":
        data["language"] = language
    try:
        with file.open("rb") as stream:
            response = client.post(PROVIDERS[settings.provider]["endpoint"], headers={"Authorization": "Bearer " + settings.key}, data=data, files={"file": (file.name, stream, "audio/mpeg")})
    except httpx.RequestError:
        # Do not retry automatically: an uncertain response may already have incurred charges.
        raise UserError("A conexão com a API foi interrompida. A parte enviada pode ter sido cobrada. Confira o provedor antes de tentar novamente.") from None
    if response.status_code in (401, 403):
        raise UserError("A API recusou a chave ou o acesso ao modelo. Verifique a configuração no Easypanel.")
    if response.status_code == 429:
        raise UserError("A API atingiu o limite de uso ou de créditos. Confira a conta do provedor e tente novamente mais tarde.")
    if response.is_error:
        raise UserError("A API não conseguiu processar o áudio. Verifique a disponibilidade do modelo e os limites da conta.")
    try:
        result = response.json()
        if not isinstance(result.get("text"), str):
            raise ValueError()
        return result
    except (ValueError, AttributeError):
        raise UserError("A API retornou uma resposta inesperada.") from None

def transcribe_chunks(files, duration, settings, language, client):
    text, segments, languages = [], [], []
    elapsed = 0.0
    for index, file in enumerate(files):
        progress(f"Transcrevendo parte {index + 1} de {len(files)}", 35 + int(60 * index / len(files)))
        piece_duration = duration_of(file)
        result = request_transcription(file, settings, language, client)
        text.append(result["text"].strip())
        if settings.timestamps:
            segments.extend(normalize_segments(result.get("segments", []), elapsed, piece_duration))
        if result.get("language"):
            languages.append(result["language"])
        elapsed += piece_duration
    combined = "\n\n".join(piece for piece in text if piece)
    if not combined:
        raise UserError("Não foi identificada fala no áudio.")
    return {"text": combined, "segments": segments, "language": languages[0] if languages else language, "duration": round(duration, 2), "provider": settings.provider, "model": settings.model}

def process(job, settings, directory):
    if not settings.key:
        raise UserError("Configure a chave da API no servidor antes de transcrever.")
    url, platform = publication_url(job["url"])
    progress("Consultando publicação", 5)
    arguments = downloader_arguments(settings)
    metadata = json.loads(command(arguments + ["--dump-single-json", "--skip-download", "--", url]))
    entries = metadata.get("entries")
    if entries:
        metadata = next((entry for entry in entries if entry), {})
    if metadata.get("is_live") or metadata.get("live_status") == "is_live":
        raise UserError("Transmissões ao vivo não são suportadas. Aguarde o vídeo ser publicado.")
    length = metadata.get("duration")
    if length and float(length) > settings.max_duration:
        raise UserError(f"O vídeo excede o limite de {settings.max_duration // 60} minutos.")
    progress("Extraindo áudio", 15)
    command(arguments + ["-f", "bestaudio/best", "--max-filesize", str(settings.max_download), "--match-filters", f"!is_live & duration <=? {settings.max_duration}", "-o", str(directory / "source.%(ext)s"), "--", url], timeout=600)
    candidates = [file for file in directory.glob("source.*") if file.suffix not in {".part", ".ytdl"} and file.is_file()]
    if len(candidates) != 1:
        raise UserError("Não foi encontrado um vídeo único com áudio dentro do limite de tamanho. Publicações com vários vídeos usam somente o primeiro.")
    if candidates[0].stat().st_size > settings.max_download:
        raise UserError("O arquivo excede o limite de tamanho configurado.")
    progress("Preparando áudio", 30)
    chunks, duration = prepare_chunks(candidates[0], settings, directory)
    with httpx.Client(timeout=httpx.Timeout(240, connect=20), follow_redirects=False) as client:
        result = transcribe_chunks(chunks, duration, settings, job["language"], client)
    return dict(result, title=str(metadata.get("title") or "Publicação")[:300], platform=platform)

def main():
    directory = Path.cwd()
    try:
        job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
        settings = Settings.load()
        result = process(job, settings, directory)
        output = {"result": result}
    except UserError as error:
        output = {"error": str(error)}
    except Exception:
        output = {"error": "A extração ou transcrição falhou. Confira a disponibilidade da publicação e a configuração do servidor. Nenhuma tentativa adicional foi feita automaticamente."}
    (directory / "result.json").write_text(json.dumps(output), encoding="utf-8")

if __name__ == "__main__":
    main()
