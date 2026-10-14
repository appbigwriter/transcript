"""Alternative public YouTube audio extraction, without paid download APIs."""
from pathlib import Path
from urllib.error import HTTPError
import socket

class YouTubeDownloadError(Exception):
    def __init__(self, message, code='youtube_alternative_failed'):
        super().__init__(message)
        self.code = code

def diagnose_error(error, stage):
    prefix = f'PyTubeFix: falha na etapa {stage}. '
    kind = type(error).__name__
    if isinstance(error, HTTPError):
        code = int(error.code)
        return YouTubeDownloadError(prefix + f'O YouTube respondeu HTTP {code}. ' + ('A origem foi limitada; aguarde antes de repetir.' if code == 429 else 'A requisição foi recusada; isso não identifica sozinho se a causa é rede, sessão ou cliente.') + ' Nenhuma chamada à API de transcrição foi feita.', f'youtube_http_{code}_{stage}')
    if kind in {'BotDetection', 'PoTokenRequired'}:
        return YouTubeDownloadError(prefix + 'O extrator recebeu um desafio antiautomação do YouTube. O sucesso em outra rede não garante acesso a partir desta VPS.', 'youtube_bot_challenge')
    if kind in {'LoginRequired', 'AgeRestrictedError', 'VideoPrivate', 'MembersOnly'}:
        return YouTubeDownloadError(prefix + 'O vídeo exige uma conta com permissão; o PyTubeFix está sendo usado sem login.', 'youtube_permission_required')
    if isinstance(error, (TimeoutError, socket.timeout)):
        return YouTubeDownloadError(prefix + 'A conexão com o YouTube excedeu o tempo limite.', 'youtube_network_timeout')
    if isinstance(error, (FileNotFoundError, PermissionError)):
        return YouTubeDownloadError(prefix + 'Verifique as dependências de execução e a permissão de escrita no container.', 'youtube_runtime_error')
    return YouTubeDownloadError(prefix + f'O extrator apresentou {kind}. Nenhuma chamada à API de transcrição foi feita.', 'youtube_extractor_error')

def download_audio(url, directory, settings):
    from pytubefix import YouTube
    def size_limit(stream, chunk, remaining):
        if stream.filesize - remaining > settings.max_download:
            raise YouTubeDownloadError('O áudio excede o limite de tamanho configurado.')
    stage = 'initialization'
    try:
        video = YouTube(url, on_progress_callback=size_limit)
        stage = 'streams'
        stream = video.streams.get_audio_only()
        if stream is None:
            raise YouTubeDownloadError('O PyTubeFix não encontrou um fluxo de áudio disponível.')
        stage = 'metadata'
        details = video.vid_info.get('videoDetails', {})
        title = str(details.get('title') or 'Vídeo do YouTube')[:300]
        duration = video.length
        if not duration or details.get('isLive'):
            raise YouTubeDownloadError('Transmissões ao vivo ou vídeos sem duração definida não são suportados.')
        if duration > settings.max_duration:
            raise YouTubeDownloadError(f'O vídeo excede o limite de {settings.max_duration // 60} minutos.')
        stage = 'size'
        if stream.filesize > settings.max_download:
            raise YouTubeDownloadError('O áudio excede o limite de tamanho configurado.')
        # Container-independent name: FFprobe/FFmpeg identify the actual media format.
        stage = 'download'
        path = Path(stream.download(output_path=str(directory), filename='youtube-audio.media', timeout=20, max_retries=0, skip_existing=False))
        if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > settings.max_download:
            raise YouTubeDownloadError('O download não gerou um arquivo de áudio válido dentro do limite.')
        return path, {'title': title, 'duration': duration, 'extractor': 'pytubefix'}
    except YouTubeDownloadError:
        raise
    except Exception as error:
        # Do not return raw exceptions: some include signed media URLs.
        raise diagnose_error(error, stage) from None
