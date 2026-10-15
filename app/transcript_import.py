"""Parse ready-made transcripts without fetching remote resources."""
import html
import json
import re
from pathlib import PurePosixPath

from .local_api import Segment


def import_transcript(filename, content):
    name = PurePosixPath(filename.replace('\\', '/')).name[:200]
    extension = PurePosixPath(name).suffix.lower()
    if extension not in {'.txt', '.srt', '.vtt', '.json'} or '\x00' in content:
        raise ValueError('Use um arquivo de texto UTF-8 nos formatos TXT, SRT, VTT ou JSON do FBR-Transcript.')
    segments = []
    title, language = name or 'Transcrição importada', 'auto'
    if extension == '.json':
        try:
            value = json.loads(content)
            if isinstance(value.get('result'), dict): value = value['result']
            text = value['text']
            if not isinstance(text, str): raise ValueError()
            title = str(value.get('title') or title)[:300]
            language = str(value.get('language') or 'auto')[:40]
            segments = [Segment.model_validate(s).model_dump() for s in value.get('segments', [])]
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('JSON inválido. Envie um resultado do FBR-Transcript com o campo text.') from None
    elif extension in {'.srt', '.vtt'}:
        def seconds(raw):
            parts = raw.replace(',', '.').split(':')
            return sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts)))
        blocks = re.split(r'\r?\n\s*\r?\n', content.strip())
        for block in blocks:
            lines = block.splitlines()
            for index, line in enumerate(lines):
                match = re.match(r'\s*((?:\d+:)?\d{2}:\d{2}[.,]\d+)\s+-->\s+((?:\d+:)?\d{2}:\d{2}[.,]\d+)', line)
                if match:
                    text = html.unescape(re.sub('<[^>]+>', '', ' '.join(lines[index + 1:]))).strip()
                    if text:
                        segments.append(Segment(start=seconds(match[1]), end=seconds(match[2]), text=text).model_dump())
                    break
        text = '\n'.join(s['text'] for s in segments)
    else:
        text = content.lstrip('\ufeff').strip()
    if not text.strip() or len(text) > 60000:
        raise ValueError('A transcrição deve conter de 1 a 60.000 caracteres. Nenhum trecho foi cortado automaticamente.')
    return dict(title=title, text=text, segments=segments, duration=max((s['end'] for s in segments), default=0),
                language=language, platform='Transcrição importada', provider='manual_import', model=None, source_type='manual_import')
