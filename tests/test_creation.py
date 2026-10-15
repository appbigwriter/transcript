import json
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import httpx
from app.main import app
from app.creation import chat, TextSettings, CreationError, split_text, process_creation, Translation
from types import SimpleNamespace
from app.transcript_import import import_transcript

class ImportTests(unittest.TestCase):
    def test_formats_and_no_silent_truncation(self):
        self.assertEqual(import_transcript('a.txt', '\ufeffOlá mundo')['text'], 'Olá mundo')
        for ext, content in [('srt','1\n00:00:01,000 --> 00:00:03,000\nOlá <b>mundo</b>'),('vtt','WEBVTT\n\n00:01.000 --> 00:03.000\nOlá mundo')]:
            result=import_transcript('a.'+ext,content)
            self.assertEqual(result['text'],'Olá mundo')
            self.assertEqual(result['segments'][0]['start'],1)
        self.assertEqual(import_transcript('a.json',json.dumps({'result':{'text':'Texto pronto','title':'Referência'}}))['title'],'Referência')
        for name,content in [('a.txt',' '),('a.json','[]'),('a.srt','texto sem tempos'),('a.exe','texto'),('a.txt','a'*60001)]:
            with self.assertRaises(ValueError): import_transcript(name,content)
        source=('Uma frase completa.\n'*1000)
        self.assertEqual(''.join(split_text(source)),source)
        self.assertTrue(all(len(x)<=6000 for x in split_text(source)))

class CreationModelTests(unittest.TestCase):
    def test_long_translation_processes_every_chunk_in_order(self):
        text = 'A long original sentence.\n' * 600
        job = dict(operation='translate', source_text=text, parameters={}, source_title='Reference')
        with patch.dict(os.environ, {'FBR_TEXT_PROVIDER':'groq','GROQ_API_KEY':'test'}), patch('app.creation.chat', side_effect=lambda c,s,o,t,p: Translation(title='Part',translation=t)) as mocked:
            result = process_creation(job, SimpleNamespace(provider='groq'), lambda *args: None)
        self.assertEqual(''.join(call.args[3] for call in mocked.call_args_list), text)
        self.assertGreater(mocked.call_count, 1)
        self.assertIn('A long original sentence.', result['text'])

    def call(self,operation,data,status=200,finish='stop'):
        def handler(request):
            self.sent=json.loads(request.content)
            return httpx.Response(status,json={'choices':[{'finish_reason':finish,'message':{'content':json.dumps(data)}}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return chat(client,TextSettings('groq','test','secret'),operation,'Texto da referência',{})
    def test_four_schemas_and_source_is_data(self):
        samples={'strategy':dict(title='Estratégia',summary='Análise',mechanisms=[dict(name='Gancho',explanation='Observação',evidence='Texto da referência')],applications=['Aplicar'],limitations=['Sem métricas']),
        'script':dict(title='Roteiro',hook_options=['Um gancho'],script='Um roteiro original com texto suficiente.',cta='Comente',production_notes=[],estimated_seconds=60),
        'translate':dict(title='Tradução',translation='Texto traduzido'),
        'contextualize':dict(title='Adaptação',adapted_text='Texto adaptado',changes=['Cenário'],assumptions=[])}
        for operation,data in samples.items():
            self.assertEqual(self.call(operation,data).title,data['title'])
            self.assertEqual(json.loads(self.sent['messages'][1]['content'])['transcricao_referencia'],'Texto da referência')
        samples['strategy']['mechanisms'][0]['evidence']='Inventado'
        with self.assertRaises(CreationError):self.call('strategy',samples['strategy'])
    def test_provider_errors_and_incomplete_results(self):
        for status in [401,403,429,400,413,500]:
            with self.assertRaises(CreationError):self.call('translate',{},status)
        with self.assertRaises(CreationError):self.call('translate',dict(title='x',translation='incompleto'),finish='length')

class CreationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'FBR_DATA_DIR':self.temp.name,'FBR_ALLOW_PUBLIC':'0','FBR_PASSWORD':'test-password-long','FBR_USERNAME':'fbr','FBR_PROVIDER':'groq','FBR_TEXT_PROVIDER':'groq','GROQ_API_KEY':'test-only','FBR_TEXT_API_KEY':''})
        self.env.start();self.worker=patch('app.main.worker_loop');self.worker.start()
        self.client=TestClient(app);self.client.__enter__();self.client.auth=('fbr','test-password-long')
    def tearDown(self):
        self.client.__exit__(None,None,None);self.worker.stop();self.env.stop();self.temp.cleanup()
    def source(self):
        data=dict(request_id='a'*32,filename='referencia.txt',title='Meu título',content='Uma transcrição pronta para analisar.')
        first=self.client.post('/api/library/import',json=data)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.client.post('/api/library/import',json=data).json()['id'],first.json()['id'])
        self.assertEqual(first.json()['result']['title'],'Meu título')
        return first.json()
    def test_library_and_creations_are_separate_and_idempotent(self):
        source=self.source()
        data=dict(request_id='b'*32,source_id=source['id'],operation='script')
        first=self.client.post('/api/creations',json=data)
        self.assertEqual(first.status_code,202,first.text)
        strategy,_=app.state.store.create_local(dict(kind='creation',operation='strategy',source_id=source['id']),10,'c'*32,'strategy',dict(title='Estratégia',text='Estratégia analisada',data={'summary':'Ideia'}))
        again=self.client.post('/api/creations',json=data)
        self.assertEqual(first.json()['id'],again.json()['id'])
        self.assertEqual(len(self.client.get('/api/library').json()['transcripts']),1)
        data['request_id']='d'*32
        second=self.client.post('/api/creations',json=data).json()
        self.assertEqual(second['strategy_id'],strategy['id'])
        self.assertEqual(app.state.store.get(source['id'])['result']['text'],source['result']['text'])
        self.assertEqual(len(self.client.get('/api/creations?source_id='+source['id']).json()['creations']),3)
        self.assertEqual(self.client.get('/api/creations/submissions/'+'b'*32).json()['id'],first.json()['id'])
    def test_context_required_and_import_without_provider(self):
        with patch.dict(os.environ,{'GROQ_API_KEY':'','FBR_TEXT_API_KEY':''}):self.source()
        source=self.source()
        response=self.client.post('/api/creations',json=dict(request_id='e'*32,source_id=source['id'],operation='contextualize'))
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.post('/api/library/import',json=dict(request_id='f'*32,filename='a.json',content='[]')).status_code,400)
        self.client.auth=None
        self.assertEqual(self.client.get('/api/library').status_code,401)

if __name__=='__main__':unittest.main()
