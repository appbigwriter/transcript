"""Alternative public YouTube audio extraction, without paid download APIs."""
from pathlib import Path

class YouTubeDownloadError(Exception):
    pass

def download_audio(url, directory, settings):
    from pytubefix import YouTube
    def size_limit(stream, chunk, remaining):
        if stream.filesize - remaining > settings.max_download:
            raise YouTubeDownloadError('O áudio excede o limite de tamanho configurado.')
    try:
        video = YouTube(url, on_progress_callback=size_limit)
        stream = video.streams.get_audio_only()
        if stream is None:
            raise YouTubeDownloadError('O PyTubeFix não encontrou um fluxo de áudio disponível.')
        duration = video.length
        if not duration or video.vid_info.get('videoDetails', {}).get('isLive'):
            raise YouTubeDownloadError('Transmissões ao vivo ou vídeos sem duração definida não são suportados.')
        if duration > settings.max_duration:
            raise YouTubeDownloadError(f'O vídeo excede o limite de {settings.max_duration // 60} minutos.')
        if stream.filesize > settings.max_download:
            raise YouTubeDownloadError('O áudio excede o limite de tamanho configurado.')
        # Container-independent name: FFprobe/FFmpeg identify the actual media format.
        path = Path(stream.download(output_path=str(directory), filename='youtube-audio.media', timeout=20, max_retries=0))
        if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > settings.max_download:
            raise YouTubeDownloadError('O download não gerou um arquivo de áudio válido dentro do limite.')
        return path, {'title': video.title, 'duration': duration, 'extractor': 'pytubefix'}
    except YouTubeDownloadError:
        raise
    except Exception as error:
        # Do not return raw exceptions: some include signed media URLs.
        raise YouTubeDownloadError('A alternativa PyTubeFix também não conseguiu obter o áudio deste vídeo. Nenhuma chamada à API de transcrição foi feita.') from None
