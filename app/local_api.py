"""Authenticated desktop ingestion; browser sessions never cross this boundary."""
import hashlib
import json
import os
import re
import secrets

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .core import LANGUAGES, publication_url


class Segment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=20000)

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('O fim do trecho deve ser posterior ao início.')
        return self


class Source(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    language: str = Field(min_length=1, max_length=40)
    duration: float = Field(ge=0, le=14400, allow_inf_nan=False)


class Transcript(Source):
    text: str = Field(min_length=1, max_length=1000000)
    segments: list[Segment] = Field(min_length=1, max_length=20000)
    source_type: str = Field(pattern=r'^captions_(manual|automatic)$')


def identifier(raw):
    if not re.fullmatch(r'[a-f0-9]{32}', raw or ''):
        raise HTTPException(400, 'Identificador local inválido.')
    return raw


def private(request):
    if request.app.state.settings.allow_public:
        raise HTTPException(403, 'A integração local exige autenticação no servidor.')


async def bounded_body(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, 'Requisição muito grande.')
    return bytes(data)


def create(request, data, request_id, fingerprint, result=None):
    try:
        return request.app.state.store.create_local(data, request.app.state.settings.max_queue, request_id, fingerprint, result)
    except LookupError as error:
        raise HTTPException(410, str(error)) from None
    except OverflowError as error:
        raise HTTPException(429, str(error)) from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


def install_local_routes(app):
    @app.get('/api/local/capabilities')
    def capabilities(request: Request):
        private(request)
        settings = request.app.state.settings
        return dict(version=1, audio_ready=bool(settings.key), max_bytes=settings.max_download,
                    max_duration=settings.max_duration, retention_hours=settings.retention // 3600)

    @app.get('/api/local/submissions/{request_id}')
    def submission(request_id: str, request: Request):
        private(request)
        found, job = request.app.state.store.local_submission(identifier(request_id))
        if not found:
            raise HTTPException(404, 'Solicitação ainda não recebida.')
        if job is None:
            raise HTTPException(410, 'Resultado expirado. Não reenvie automaticamente.')
        return job

    @app.post('/api/local/transcripts')
    async def transcript(request: Request):
        private(request)
        request_id = identifier(request.headers.get('x-local-request-id'))
        try:
            body = Transcript.model_validate_json(await bounded_body(request, 2000000))
            url, platform = publication_url(body.url)
        except (ValueError, ValidationError):
            raise HTTPException(400, 'Transcrição ou link inválido.') from None
        if not body.text.strip() or any(s.end > request.app.state.settings.max_duration + 1 for s in body.segments):
            raise HTTPException(400, 'Texto vazio ou duração acima do limite.')
        if body.duration > request.app.state.settings.max_duration:
            raise HTTPException(400, 'Duração acima do limite.')
        payload = body.model_dump()
        payload['url'] = url
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result = dict(text=body.text, segments=[s.model_dump() for s in body.segments], title=body.title,
                      platform=platform, language=body.language, duration=body.duration, source_url=url,
                      source_type=body.source_type, provider='platform_captions', model=None, extractor='desktop')
        job, _ = create(request, dict(kind='local_transcript', url=url, platform=platform, language=body.language),
                        request_id, fingerprint, result)
        return job

    @app.post('/api/local/audio', status_code=202)
    async def audio(request: Request):
        from urllib.parse import unquote
        private(request)
        request_id = identifier(request.headers.get('x-local-request-id'))
        digest = request.headers.get('x-content-sha256', '')
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise HTTPException(400, 'Hash do áudio inválido.')
        try:
            source = Source.model_validate_json(unquote(request.headers.get('x-local-source', '')))
            url, platform = publication_url(source.url)
        except (ValueError, ValidationError):
            raise HTTPException(400, 'Metadados do áudio inválidos.') from None
        settings = request.app.state.settings
        if source.language not in LANGUAGES or source.duration > settings.max_duration:
            raise HTTPException(400, 'Idioma ou duração inválidos.')
        if not settings.key:
            raise HTTPException(503, 'Configure a chave de transcrição no servidor.')
        fingerprint = hashlib.sha256((json.dumps(dict(source.model_dump(), url=url), sort_keys=True) + digest).encode()).hexdigest()
        upload_id = secrets.token_hex(16)
        directory = settings.data_dir / 'uploads'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / (upload_id + '.media')
        accepted = False
        try:
            total, checksum = 0, hashlib.sha256()
            with path.open('xb') as stream:
                os.chmod(path, 0o600)
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > settings.max_download:
                        raise HTTPException(413, 'O áudio excede o limite do servidor.')
                    checksum.update(chunk)
                    stream.write(chunk)
            if not total or checksum.hexdigest() != digest:
                raise HTTPException(400, 'Áudio vazio ou transferência incompleta.')
            job, accepted = create(request, dict(kind='upload', url=url, platform=platform, language=source.language,
                upload_id=upload_id, upload_name=source.title, source_url=url, source_platform=platform,
                source_type='speech_to_text', extractor='desktop'), request_id, fingerprint)
            return job
        finally:
            if not accepted:
                path.unlink(missing_ok=True)
