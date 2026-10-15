import hashlib
import json
import re
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .creation import OPERATIONS, TextSettings
from .local_api import bounded_body, create, private
from .transcript_import import import_transcript


class CreationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern='^[a-f0-9]{32}$')
    source_id: str = Field(pattern='^[a-f0-9]{32}$')
    operation: Literal['strategy', 'script', 'translate', 'contextualize']
    context: str = Field(default='', max_length=6000)
    audience: str = Field(default='', max_length=300)
    tone: Literal['didático', 'conversacional', 'inspirador', 'provocativo', 'profissional'] = 'conversacional'
    platform: Literal['Reels', 'Shorts', 'TikTok', 'Facebook', 'X', 'Vídeo longo'] = 'Reels'
    seconds: int = Field(default=60, ge=15, le=600)


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern='^[a-f0-9]{32}$')
    filename: str = Field(min_length=1, max_length=300)
    title: str = Field(default='', max_length=300)
    content: str = Field(min_length=1, max_length=1000000)


def install_creation_routes(app, enqueue, job_or_404):
    @app.get('/api/library')
    def library(request: Request):
        private(request)
        rows = request.app.state.store.recent()
        transcripts = [dict(id=j['id'], title=j['result'].get('title', 'Transcrição'), platform=j['result'].get('platform'),
                            created=j['created'], characters=len(j['result']['text']), source_type=j['result'].get('source_type'))
                       for j in rows if j['status'] == 'done' and j.get('result', {}).get('text') and j.get('kind') != 'creation']
        return {'transcripts': transcripts}

    @app.post('/api/library/import')
    async def import_ready(request: Request):
        private(request)
        try:
            body = ImportRequest.model_validate_json(await bounded_body(request, 2000000))
            result = import_transcript(body.filename, body.content)
            if body.title.strip():
                result['title'] = body.title.strip()
        except (ValueError, ValidationError) as error:
            detail = str(error) if isinstance(error, ValueError) and not isinstance(error, ValidationError) else 'Arquivo ou solicitação inválidos.'
            raise HTTPException(400, detail) from None
        fingerprint = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        job, _ = create(request, dict(kind='imported_transcript', url=None, platform='Transcrição importada', language=result['language']),
                        body.request_id, fingerprint, result)
        return job

    @app.get('/api/creations')
    def creations(request: Request, source_id: str | None = None):
        private(request)
        if source_id and not re.fullmatch('[a-f0-9]{32}', source_id):
            raise HTTPException(400, 'Transcrição inválida.')
        return {'creations': [dict(id=j['id'], source_id=j.get('source_id'), source_title=j.get('source_title'), operation=j['operation'],
                                   status=j['status'], stage=j['stage'], created=j['created'], title=j.get('result', {}).get('title'))
                              for j in request.app.state.store.recent() if j.get('kind') == 'creation' and (not source_id or j.get('source_id') == source_id)]}

    @app.post('/api/creations', status_code=202)
    async def generate(request: Request):
        private(request)
        try:
            body = CreationRequest.model_validate_json(await bounded_body(request, 2000000))
            config = TextSettings.load(request.app.state.settings)
        except (ValueError, ValidationError):
            raise HTTPException(400, 'Confira a transcrição, a ação, os campos do briefing e a configuração do modelo.') from None
        if body.operation == 'contextualize' and not body.context.strip():
            raise HTTPException(400, 'Informe o cenário para contextualizar: negócio, público, objetivo e situação de uso.')
        if not config.key:
            raise HTTPException(503, 'Configure a chave do provedor de texto no servidor.')
        source = job_or_404(request, body.source_id)
        if source['status'] != 'done' or not source.get('result', {}).get('text') or source.get('kind') == 'creation':
            raise HTTPException(400, 'Selecione uma transcrição concluída como base.')
        text = source['result']['text']
        if len(text) > 60000:
            raise HTTPException(400, 'A transcrição excede 60.000 caracteres. Importe um trecho para trabalhar; nada foi cortado.')
        parameters = dict(context=body.context.strip(), audience=body.audience.strip(), tone=body.tone,
                          platform=body.platform, requested_seconds=body.seconds)
        payload = dict(kind='creation', operation=body.operation, source_id=body.source_id,
                       source_title=source['result'].get('title', 'Transcrição'), source_text=text,
                       source_url=source.get('url'), parameters=parameters, url=None, platform='Criação', language='pt')
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if body.operation == 'script':
            strategy = next((j for j in request.app.state.store.recent() if j.get('kind') == 'creation'
                             and j.get('operation') == 'strategy' and j.get('source_id') == body.source_id
                             and j['status'] == 'done'), None)
            if strategy:
                payload['strategy_id'] = strategy['id']
                payload['strategy_reference'] = strategy['result'].get('data')
        return enqueue(request, payload, body.request_id, fingerprint)

    @app.get('/api/creations/submissions/{request_id}')
    def recover(request_id: str, request: Request):
        private(request)
        if not re.fullmatch('[a-f0-9]{32}', request_id):
            raise HTTPException(400, 'Identificador inválido.')
        found, job = request.app.state.store.local_submission(request_id)
        if not found:
            raise HTTPException(404, 'Solicitação ainda não recebida.')
        if not job:
            raise HTTPException(410, 'Criação expirada. Não gere novamente automaticamente.')
        if job.get('kind') != 'creation':
            raise HTTPException(404, 'Criação não encontrada.')
        return job
