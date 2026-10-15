"""Run in a disposable process; secrets stay in server environment."""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import httpx

from .core import PROVIDERS, Settings, normalize_segments, publication_url
from .sessions import SessionStore, platform_id, filtered_cookies

CHUNK_SECONDS = 600

class UserError(Exception):
    def __init__(self, message, code='processing_error'):
        super().__init__(message)
        self.code = code

def extraction_error(stderr):
    """Recognize concrete signals, without exposing URLs, cookies or raw stderr."""
    message = stderr.lower()
    rules = [
        (("not a bot", 'bot detection', 'captcha', 'automated requests'), 'bot_challenge', 'A plataforma solicitou uma verificação antiautomação. Isso não comprova senha ou cookies incorretos. Renovar cookies sozinho pode não resolver; é necessário validar a sessão e o acesso a partir do servidor.'),
        (('cookies are no longer valid', 'cookies have expired', 'cookie has expired', 'invalid cookies'), 'session_expired', 'O extrator informou que a sessão de cookies não é mais válida. Faça login na plataforma e sincronize ou importe uma nova sessão.'),
        (('requested format is not available', 'no video formats found'), 'format_unavailable', 'O extrator não encontrou um formato de mídia disponível. Isso pode ser uma limitação do cliente/extrator ou do acesso ao vídeo; não é uma confirmação de erro de login.'),
        (('http error 429', 'too many requests'), 'rate_limited', 'A plataforma limitou as requisições. Aguarde antes de testar novamente; repetir o login ou reenviar a mesma tarefa não resolve necessariamente esse limite.'),
        (('private video', 'this video is private', 'private account'), 'private_content', 'A publicação é privada. A sessão utilizada precisa ter permissão explícita para acessá-la.'),
        (('video unavailable', 'video is unavailable', 'video has been removed', 'no longer available', 'does not exist'), 'content_unavailable', 'A plataforma informou que a publicação está indisponível ou foi removida. Confira o mesmo link no navegador.'),
        (('unsupported url', 'no suitable extractor'), 'unsupported_publication', 'O extrator não reconheceu esta publicação ou variação de link. O suporte desta plataforma precisa ser verificado antes de repetir a tentativa.'),
        (('certificate verify failed', 'ssl certificate'), 'tls_error', 'Falha na verificação TLS ao acessar a plataforma. Confira os certificados e a rede do servidor; não é um erro de cookies.'),
        (('http error 403', '403 forbidden'), 'access_denied', 'A plataforma recusou a requisição com HTTP 403. A resposta não permite concluir se a causa é sessão, permissão, token do cliente ou bloqueio de rede.'),
        (('sign in to confirm your age', 'age-restricted'), 'age_restricted', 'O vídeo exige comprovação de idade ou uma conta autorizada. A sessão atual não foi suficiente para obter acesso.'),
        (('login required', 'login_required', 'sign in', 'log in', 'authentication required', 'only available for registered users'), 'authentication_required', 'A plataforma solicitou autenticação para esta publicação. Confira se a sessão da plataforma foi importada ou sincronizada e se essa conta tem acesso ao mesmo link.'),
        (('ffmpeg not found', 'ffprobe not found'), 'missing_dependency', 'FFmpeg ou FFprobe não foi encontrado no servidor. Confira as dependências do container.'),
        (('timed out', 'connection refused', 'unable to resolve', 'name resolution'), 'network_error', 'Falha de rede ao acessar a plataforma. Verifique conectividade, DNS e disponibilidade antes de renovar a sessão.'),
    ]
    for signals, code, text in rules:
        if any(signal in message for signal in signals):
            return UserError(text, code)
    return UserError('A extração falhou sem uma causa reconhecida. Não há evidência suficiente para atribuir a falha a cookies. É preciso analisar este link e a etapa de extração antes de repetir a tentativa.', 'extraction_unknown')

def progress(stage, percent):
    print(json.dumps({"stage": stage, "progress": percent}), flush=True)

def command(arguments, timeout=300):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UserError("A operação excedeu o tempo limite. Isso não comprova um erro de autenticação.", 'operation_timeout') from None
    if result.returncode:
        # Downloader stderr can contain sensitive URLs/session information. Do not return it.
        if Path(arguments[0]).stem.lower() in {'ffmpeg', 'ffprobe'}:
            raise UserError('O arquivo não pôde ser lido ou convertido pelo FFmpeg/FFprobe. Confira se contém áudio e se está íntegro.', 'invalid_media')
        raise extraction_error(result.stderr)
    return result.stdout

def downloader_arguments(settings, platform=None):
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
    ]
    # Let yt-dlp pick current supported clients; forced mobile clients may reject cookies.
    if platform:
        sessions = SessionStore(settings)
        if sessions.enabled and sessions.has_override(platform):
            try:
                if sessions.materialize(platform, Path('session.txt')):
                    args += ['--cookies', str(Path('session.txt').resolve())]
            except ValueError as error:
                raise UserError(str(error)) from None
            return args
    if settings.cookies_file:
        source = Path(settings.cookies_file)
        if not source.is_file():
            raise UserError("O arquivo de cookies configurado no servidor não foi encontrado.", 'session_file_missing')
        # yt-dlp writes updated cookies; work with a disposable copy of the mounted session.
        import shutil
        destination = Path("session.txt")
        if platform:
            try:
                text, _ = filtered_cookies(source.read_text(encoding='utf-8-sig'), platform)
            except ValueError:
                # A legacy file for a different platform is not applied to this request.
                return args
            destination.write_text(text, encoding='utf-8')
            os.chmod(destination, 0o600)
        else:
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

def extract_ytdlp_source(url, platform, settings, directory):
    progress("Consultando publicação", 5)
    arguments = downloader_arguments(settings, platform_id(platform))
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
    return candidates[0], metadata

def extract_source(url, platform, settings, directory):
    engine = os.getenv('FBR_YOUTUBE_ENGINE', 'auto').strip().lower()
    if engine not in {'auto', 'yt-dlp', 'pytubefix'}:
        raise UserError('FBR_YOUTUBE_ENGINE deve ser auto, yt-dlp ou pytubefix.', 'invalid_configuration')
    if platform != 'YouTube' or engine == 'yt-dlp':
        return extract_ytdlp_source(url, platform, settings, directory)
    if engine == 'auto':
        try:
            return extract_ytdlp_source(url, platform, settings, directory)
        except UserError as error:
            if error.code not in {'bot_challenge', 'format_unavailable', 'access_denied', 'extraction_unknown', 'network_error', 'session_file_missing'}:
                raise
            progress('Tentando alternativa de extração do YouTube', 18)
    from .youtube import download_audio, YouTubeDownloadError
    try:
        return download_audio(url, directory, settings)
    except YouTubeDownloadError as error:
        raise UserError(str(error), error.code) from None

def process(job, settings, directory):
    if job.get('kind') == 'creation':
        from .creation import process_creation, CreationError
        try:
            return process_creation(job, settings, progress)
        except (CreationError, ValueError) as error:
            raise UserError(str(error), 'creation_error') from None
    if job.get('kind') == 'extraction_check':
        url, platform = publication_url(job['url'])
        source, metadata = extract_source(url, platform, settings, directory)
        return {'kind': 'extraction_check', 'title': str(metadata.get('title') or 'Publicação')[:300], 'platform': platform, 'extractor': metadata.get('extractor', 'yt-dlp'), 'duration': duration_of(source), 'bytes': source.stat().st_size, 'notice': 'Áudio baixado e validado no servidor. Nenhuma API de transcrição foi chamada.'}
    if job.get('kind') == 'session_check':
        url, platform = publication_url(job['url'])
        arguments = downloader_arguments(settings, platform_id(platform))
        progress('Testando acesso à publicação', 10)
        metadata = json.loads(command(arguments + ['--dump-single-json', '--skip-download', '--', url], timeout=90))
        return {'kind': 'session_check', 'accessible': True, 'platform': platform, 'title': str(metadata.get('title') or 'Publicação')[:300], 'notice': 'Link de teste acessível. Isso não comprova login nem garante download de outros vídeos.'}
    if not settings.key:
        raise UserError("Configure a chave da API no servidor antes de transcrever.")
    if job.get('kind') == 'upload':
        progress('Preparando arquivo enviado', 30)
        source = directory / 'upload.media'
        if not source.is_file():
            raise UserError('O arquivo enviado não está mais disponível. Envie-o novamente.')
        chunks, duration = prepare_chunks(source, settings, directory)
        with httpx.Client(timeout=httpx.Timeout(240, connect=20), follow_redirects=False) as client:
            result = transcribe_chunks(chunks, duration, settings, job['language'], client)
        return dict(result, title=job.get('upload_name') or 'Arquivo enviado', platform=job.get('source_platform', 'Arquivo enviado'),
                    source_url=job.get('source_url'), source_type='speech_to_text', extractor=job.get('extractor', 'upload'))
    url, platform = publication_url(job["url"])
    source, metadata = extract_source(url, platform, settings, directory)
    progress("Preparando áudio", 30)
    chunks, duration = prepare_chunks(source, settings, directory)
    with httpx.Client(timeout=httpx.Timeout(240, connect=20), follow_redirects=False) as client:
        result = transcribe_chunks(chunks, duration, settings, job["language"], client)
    return dict(result, title=str(metadata.get("title") or "Publicação")[:300], platform=platform, extractor=metadata.get('extractor', 'yt-dlp'))

def main():
    directory = Path.cwd()
    try:
        job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
        settings = Settings.load()
        result = process(job, settings, directory)
        output = {"result": result}
    except UserError as error:
        output = {"error": str(error), 'error_code': error.code}
    except Exception:
        output = {"error": "A extração ou transcrição falhou. Confira a disponibilidade da publicação e a configuração do servidor. Nenhuma tentativa adicional foi feita automaticamente."}
    (directory / "result.json").write_text(json.dumps(output), encoding="utf-8")

if __name__ == "__main__":
    main()
