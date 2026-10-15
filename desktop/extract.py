"""Caption-first extraction. Only exported artifacts may leave the computer."""
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app.core import publication_url


class ExtractionFailure(Exception):
    pass


def binary(name):
    bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'bin' / (name + '.exe')
    return str(bundled) if bundled.is_file() else shutil.which(name)


def clean_text(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', text))).strip()


def normalize_cues(cues, deduplicate=True):
    """Remove rolling-caption overlap without deleting later spoken repetitions."""
    result = []
    previous_words, previous_end = [], -1
    for cue in sorted(cues, key=lambda c: c['start']):
        text = clean_text(cue['text'])
        start, end = float(cue['start']), float(cue['end'])
        if not text or start < 0 or end <= start:
            continue
        words = text.split()
        original_words = words[:]
        if deduplicate and previous_words and start <= previous_end + .2:
            for length in range(min(len(previous_words), len(words)), 0, -1):
                if previous_words[-length:] == words[:length]:
                    words = words[length:]
                    break
        previous_words, previous_end = original_words, end
        if words:
            result.append(dict(start=round(start, 3), end=round(end, 3), text=' '.join(words)))
    return result


def parse_caption(path, deduplicate=True):
    raw = Path(path).read_text(encoding='utf-8-sig')
    cues = []
    if Path(path).suffix == '.json3':
        for event in json.loads(raw).get('events', []):
            start = event.get('tStartMs', 0) / 1000
            end = start + event.get('dDurationMs', 0) / 1000
            cues.append(dict(start=start, end=end, text=''.join(s.get('utf8', '') for s in event.get('segs', []))))
    else:
        def seconds(value):
            parts = value.replace(',', '.').split(':')
            return sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts)))
        lines = raw.splitlines()
        index = 0
        while index < len(lines):
            match = re.match(r'\s*((?:\d+:)?\d{2}:\d{2}[.,]\d+)\s+-->\s+((?:\d+:)?\d{2}:\d{2}[.,]\d+)', lines[index])
            index += 1
            if not match:
                continue
            content = []
            while index < len(lines) and lines[index].strip():
                content.append(lines[index])
                index += 1
            cues.append(dict(start=seconds(match[1]), end=seconds(match[2]), text=' '.join(content)))
    segments = normalize_cues(cues, deduplicate)
    if not segments:
        raise ExtractionFailure('A legenda não contém trechos utilizáveis.')
    return segments


def choose_caption(info, language):
    preferred = [language] if language != 'auto' else [info.get('language') or '', 'pt', 'en']
    candidates = []
    for field, kind in [('subtitles', 'manual'), ('automatic_captions', 'automatic')]:
        for code, tracks in (info.get(field) or {}).items():
            if code == 'live_chat' or not any(t.get('ext') in {'vtt', 'json3', 'srt'} for t in tracks):
                continue
            score = next((i for i, p in enumerate(preferred) if p and (code == p or code.startswith(p + '-'))), 20)
            # Within the requested language, prefer an authored track.
            candidates.append((score, kind != 'manual', not code.endswith('-orig'), code, kind))
    if not candidates:
        return None
    chosen = min(candidates)
    return chosen[3], chosen[4]


class QuietLogger:
    def debug(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def extract(url, language, config, directory, emit=lambda stage: None):
    import yt_dlp
    url, platform = publication_url(url)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = binary('ffmpeg')
    node = binary('node')
    max_duration = min(int(config.get('max_duration', 3600)), 14400)
    max_bytes = min(int(config.get('max_bytes', 150 * 1024 * 1024)), 1000 * 1024 * 1024)
    options = dict(quiet=True, no_warnings=True, logger=QuietLogger(), noplaylist=True, playlistend=1,
                   socket_timeout=20, retries=1, extractor_retries=1, max_filesize=max_bytes,
                   outtmpl=str(directory / 'source.%(ext)s'), windowsfilenames=True)
    browser = config.get('browser', 'none')
    if browser != 'none':
        if browser not in {'firefox', 'chrome', 'edge'}:
            raise ExtractionFailure('Navegador não suportado.')
        options['cookiesfrombrowser'] = (browser, config.get('profile') or None, None, None)
    if config.get('cookies_file'):
        # yt-dlp saves cookie jars on exit. Copy locally so the source is untouched.
        target = directory / 'session.cookies'
        shutil.copyfile(config['cookies_file'], target)
        options.pop('cookiesfrombrowser', None)
        options['cookiefile'] = str(target)
    if node:
        options['js_runtimes'] = {'node': {'path': node}}
    if ffmpeg:
        options['ffmpeg_location'] = str(Path(ffmpeg).parent)
    try:
        emit('Consultando publicação e legendas')
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
            if info.get('entries') is not None:
                info = next((entry for entry in info['entries'] if entry), None)
            if not info:
                raise ExtractionFailure('Nenhuma publicação única foi encontrada.')
            if info.get('is_live') or info.get('live_status') == 'is_live':
                raise ExtractionFailure('Aguarde a transmissão ao vivo terminar.')
            duration = float(info.get('duration') or 0)
            if duration > max_duration:
                raise ExtractionFailure('A publicação excede a duração permitida pelo servidor.')
            title = str(info.get('title') or 'Publicação')[:300]
            chosen = choose_caption(info, language)
            if chosen:
                code, kind = chosen
                emit('Obtendo legenda existente')
                downloader.params.update(skip_download=True, writesubtitles=True, writeautomaticsub=True,
                                         subtitleslangs=[code], subtitlesformat='json3/vtt/srt')
                try:
                    downloader.process_ie_result(dict(info), download=True)
                    paths = list(directory.glob('source.*.json3')) + list(directory.glob('source.*.vtt')) + list(directory.glob('source.*.srt'))
                    if paths:
                        segments = parse_caption(paths[0], deduplicate=kind == 'automatic')
                        if segments[-1]['end'] > max_duration + 1:
                            raise ExtractionFailure('A legenda excede a duração permitida.')
                        return dict(kind='transcript', url=url, platform=platform, title=title,
                                    language=code.removesuffix('-orig'), duration=duration or segments[-1]['end'],
                                    text='\n'.join(s['text'] for s in segments), segments=segments,
                                    source_type='captions_' + kind)
                except Exception:
                    emit('Legenda indisponível; preparando alternativa por áudio')
            if not config.get('allow_audio', True):
                raise ExtractionFailure('Não há legenda utilizável. Habilite a alternativa por áudio para continuar.')
            if not ffmpeg:
                raise ExtractionFailure('FFmpeg não encontrado. Execute a instalação do ambiente local.')
            emit('Obtendo áudio na rede local')
            downloader.params.update(skip_download=False, writesubtitles=False, writeautomaticsub=False,
                                     format='bestaudio/best', match_filter=yt_dlp.utils.match_filter_func(f'!is_live & duration <=? {max_duration}'))
            downloader.process_ie_result(dict(info), download=True)
        sources = [p for p in directory.glob('source.*') if p.suffix.lower() in {'.m4a', '.webm', '.mp4', '.mp3', '.ogg', '.opus', '.mkv', '.aac', '.wav'}]
        if len(sources) != 1 or sources[0].stat().st_size > max_bytes:
            raise ExtractionFailure('O áudio não foi obtido dentro do limite permitido.')
        emit('Preparando áudio para o servidor')
        output = directory / 'audio.mp3'
        completed = subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(sources[0]),
            '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '48k', '-t', str(max_duration + 1), str(output)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if completed.returncode or not output.is_file() or output.stat().st_size > max_bytes:
            raise ExtractionFailure('Não foi possível preparar o áudio.')
        probe = binary('ffprobe')
        if not probe:
            raise ExtractionFailure('FFprobe não encontrado. Execute a instalação local.')
        measured = subprocess.check_output([probe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(output)],
            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        duration = float(measured)
        if duration <= 0 or duration > max_duration + .5:
            raise ExtractionFailure('A duração do áudio está fora do limite permitido.')
        return dict(kind='audio', url=url, platform=platform, title=title, language=language, duration=duration, audio=str(output))
    except ExtractionFailure:
        raise
    except Exception as error:
        message = str(error).lower()
        if any(word in message for word in ('cookie', 'decrypt', 'dpapi', 'keyring')):
            raise ExtractionFailure('Não foi possível ler a sessão. Feche o navegador ou use um perfil Firefox/local cookies.txt. Chrome e Edge podem impedir a leitura de cookies no Windows.') from None
        if any(word in message for word in ('bot', 'captcha', 'sign in', 'login', 'private', '403', '429')):
            raise ExtractionFailure('A plataforma recusou o acesso. Entre na sua conta no navegador escolhido e confira se a publicação abre nesta rede.') from None
        raise ExtractionFailure('Não foi possível obter esta publicação. Confira o link, a conexão e a sessão selecionada.') from None
    finally:
        (directory / 'session.cookies').unlink(missing_ok=True)
