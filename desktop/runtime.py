"""Persistent local queue, Windows-protected credentials and server transport."""
import base64
import ctypes
import hashlib
import json
import multiprocessing
import os
import secrets
import sqlite3
import subprocess
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.core import publication_url, subtitles
from .extract import extract


def home():
    path = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'FBR-Transcript-Local'
    path.mkdir(parents=True, exist_ok=True)
    return path


def protect_password(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('O armazenamento de senha está disponível apenas no Windows.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_byte))]
    raw = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
        raise ValueError('O Windows não conseguiu proteger/abrir a senha deste usuário.')
    try:
        result = ctypes.string_at(destination.data, destination.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(destination.data, ctypes.c_void_p))


def load_config():
    defaults = dict(server='https://transcript.fbr.news', username='fbr', password='', browser='none',
                    profile='', cookies_file='', language='auto', allow_audio=True, send=True, remember=False)
    try:
        stored = json.loads((home() / 'config.json').read_text(encoding='utf-8'))
        encrypted = stored.pop('protected_password', '')
        defaults.update(stored)
        if encrypted:
            defaults['password'] = protect_password(encrypted, decrypt=True)
    except (OSError, ValueError, TypeError):
        pass
    return defaults


def server_url(value):
    parsed = urlsplit(value.strip())
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Informe apenas a URL do servidor, sem caminho, usuário ou senha.')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}):
        raise ValueError('Use HTTPS para conectar ao servidor.')
    return value.strip().rstrip('/')


def save_config(config):
    stored = dict(config)
    password = stored.pop('password', '')
    if stored.get('remember') and password:
        stored['protected_password'] = protect_password(password)
    path = home() / 'config.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(stored), encoding='utf-8')
    temporary.replace(path)


class Queue:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else home()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'queue.sqlite3'
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            for row in c.execute('SELECT id,payload FROM tasks').fetchall():
                data = json.loads(row[1])
                if data['status'] in {'extracting', 'submitting'}:
                    data['status'] = 'uncertain' if data['status'] == 'submitting' else 'pending'
                    data['stage'] = 'Interrompido; use Iniciar / retomar'
                    c.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(data), row[0]))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def all(self):
        with self.connect() as c:
            return [json.loads(row[0]) for row in c.execute('SELECT payload FROM tasks ORDER BY rowid')]

    def get(self, identifier):
        return next((t for t in self.all() if t['id'] == identifier), None)

    def update(self, identifier, **changes):
        with self.connect() as c:
            old = json.loads(c.execute('SELECT payload FROM tasks WHERE id=?', (identifier,)).fetchone()[0])
            old.update(changes)
            c.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(old), identifier))
        return old

    def add(self, links):
        validated = [publication_url(link) for link in links]
        existing = {t['url'] for t in self.all()}
        count = 0
        with self.connect() as c:
            for url, platform in validated:
                if url in existing:
                    continue
                data = dict(id=secrets.token_hex(16), url=url, platform=platform, status='pending',
                            stage='Aguardando', created=time.time(), remote_id=None, artifact=None)
                c.execute('INSERT INTO tasks VALUES (?,?)', (data['id'], json.dumps(data)))
                existing.add(url)
                count += 1
        return count


class Server:
    def __init__(self, config):
        self.client = httpx.Client(base_url=server_url(config['server']), auth=(config['username'], config['password']),
                                   timeout=httpx.Timeout(300, connect=20), follow_redirects=False)

    def close(self):
        self.client.close()

    def checked(self, response):
        if response.status_code == 401:
            raise ValueError('Login do Transcript recusado. Confira usuário e senha do sistema principal.')
        if response.status_code == 404:
            raise ValueError('Atualize o servidor: a integração local ainda não está disponível nesse endereço.')
        if response.status_code == 410:
            raise ValueError('O resultado expirou no servidor. Não haverá novo envio automático.')
        if response.is_error or response.is_redirect:
            try:
                detail = response.json().get('detail')
            except ValueError:
                detail = None
            raise ValueError(detail[:350] if isinstance(detail, str) else f'O servidor recusou a operação (HTTP {response.status_code}).')
        return response.json()

    def capabilities(self):
        return self.checked(self.client.get('/api/local/capabilities'))

    def lookup(self, identifier):
        response = self.client.get('/api/local/submissions/' + identifier)
        return None if response.status_code == 404 else self.checked(response)

    def submit(self, identifier, artifact):
        previous = self.lookup(identifier)
        if previous:
            return previous
        headers = {'x-local-request-id': identifier}
        source = {key: artifact[key] for key in ('url', 'title', 'language', 'duration')}
        if artifact['kind'] == 'transcript':
            source.update({key: artifact[key] for key in ('text', 'segments', 'source_type')})
            return self.checked(self.client.post('/api/local/transcripts', headers=headers, json=source))
        path = Path(artifact['audio'])
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                checksum.update(chunk)
        headers.update({'x-content-sha256': checksum.hexdigest(), 'x-local-source': quote(json.dumps(source)),
                        'content-type': 'audio/mpeg', 'content-length': str(path.stat().st_size)})
        with path.open('rb') as stream:
            response = self.client.post('/api/local/audio', headers=headers, content=iter(lambda: stream.read(65536), b''))
        return self.checked(response)

    def job(self, identifier):
        return self.checked(self.client.get('/api/jobs/' + identifier))


def extract_child(url, language, config, directory, pipe):
    try:
        artifact = extract(url, language, config, Path(directory), lambda stage: pipe.send(('stage', stage)))
        pipe.send(('artifact', artifact))
    except Exception as error:
        pipe.send(('error', str(error) if isinstance(error, ValueError) or error.__class__.__name__ == 'ExtractionFailure' else 'A extração local falhou.'))
    finally:
        pipe.close()


def smoke_child(pipe):
    """Verify the portable build can spawn an extractor process."""
    import yt_dlp
    from .extract import binary
    pipe.send(bool(yt_dlp.YoutubeDL) and bool(binary('ffmpeg')))
    pipe.close()


def write_exports(directory, result):
    directory = Path(directory)
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'transcricao.txt').write_text(result['text'], encoding='utf-8')
    if result.get('segments'):
        (directory / 'legendas.srt').write_text(subtitles(result['segments']), encoding='utf-8')


class Runner:
    def __init__(self, queue):
        self.queue = queue
        self.stop = threading.Event()
        self.thread = None
        self.process = None

    def start(self, config):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, args=(dict(config),), daemon=True)
        self.thread.start()

    def pause(self):
        self.stop.set()
        process = self.process
        self.terminate(process)

    @staticmethod
    def terminate(process):
        if process and process.is_alive():
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()

    def collect(self, task, config, directory):
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        child_config = {k: config[k] for k in ('browser', 'profile', 'cookies_file', 'allow_audio', 'max_duration', 'max_bytes') if k in config}
        process = context.Process(target=extract_child, args=(task['url'], config['language'], child_config, str(directory), sender))
        self.process = process
        process.start()
        sender.close()
        artifact = None
        deadline = time.monotonic() + 1200
        try:
            while True:
                if self.stop.is_set():
                    self.pause()
                    raise InterruptedError('Pausado; a extração pode ser retomada.')
                if time.monotonic() > deadline:
                    self.pause()
                    raise ValueError('A extração excedeu 20 minutos e foi interrompida.')
                try:
                    available = receiver.poll(.2)
                except (EOFError, OSError):
                    break
                if available:
                    try:
                        kind, value = receiver.recv()
                    except (EOFError, OSError):
                        break
                    if kind == 'stage': self.queue.update(task['id'], stage=value)
                    elif kind == 'artifact': artifact = value
                    else: raise ValueError(value)
                elif not process.is_alive():
                    break
            if artifact is None:
                raise ValueError('A extração foi interrompida sem resultado.')
            return artifact
        finally:
            process.join(timeout=2)
            if process.is_alive():
                self.terminate(process)
            process.join(timeout=10)
            receiver.close()
            self.process = None
            # Raw media/caption files and temporary cookie copies are never retained.
            for path in directory.iterdir():
                if path.name.startswith('source.') or path.name == 'session.cookies':
                    if path.is_file(): path.unlink(missing_ok=True)

    def run(self, config):
        server = None
        try:
            if config['send']:
                server = Server(config)
                limits = server.capabilities()
                config.update(max_duration=limits['max_duration'], max_bytes=limits['max_bytes'])
            for task in self.queue.all():
                if self.stop.is_set(): break
                if task['status'] in {'done', 'failed'}: continue
                directory = self.queue.directory / 'outputs' / task['id']
                directory.mkdir(parents=True, exist_ok=True)
                try:
                    # Recover a possibly accepted submission before any new extraction/upload.
                    remote = server.lookup(task['id']) if server and task['status'] in {'uncertain', 'remote'} else None
                    if task['remote_id'] and remote is None:
                        raise ValueError('A tarefa remota não está mais disponível. Não será reenviada automaticamente.')
                    artifact = task.get('artifact')
                    if remote is None:
                        if artifact is None:
                            self.queue.update(task['id'], status='extracting', stage='Iniciando coleta local')
                            artifact = self.collect(task, config, directory)
                            self.queue.update(task['id'], artifact=artifact, status='ready', stage='Coleta concluída')
                            (directory / 'artifact.json').write_text(json.dumps(artifact, ensure_ascii=False), encoding='utf-8')
                        if not server:
                            if artifact['kind'] == 'transcript': write_exports(directory, artifact)
                            self.queue.update(task['id'], status='done', stage='Salvo localmente — sem envio ao servidor')
                            continue
                        if self.stop.is_set(): break
                        self.queue.update(task['id'], status='submitting', stage='Enviando ao Transcript')
                        remote = server.submit(task['id'], artifact)
                    self.queue.update(task['id'], status='remote', remote_id=remote['id'], stage=remote['stage'])
                    while remote['status'] not in {'done', 'failed'}:
                        if self.stop.wait(2): break
                        remote = server.job(remote['id'])
                        self.queue.update(task['id'], stage=remote['stage'])
                    if self.stop.is_set(): break
                    if remote['status'] == 'failed':
                        raise ValueError(remote.get('error') or 'A transcrição falhou no servidor.')
                    write_exports(directory, remote['result'])
                    self.queue.update(task['id'], status='done', stage='Transcrição pronta e salva', result=remote['result'])
                except InterruptedError as error:
                    self.queue.update(task['id'], status='pending', stage=str(error))
                    break
                except httpx.RequestError:
                    status = 'remote' if self.queue.get(task['id']).get('remote_id') else 'uncertain'
                    self.queue.update(task['id'], status=status, stage='Conexão interrompida. Retome para consultar antes de reenviar.')
                    break
                except Exception as error:
                    if not isinstance(error, ValueError):
                        diagnostic = dict(error_type=type(error).__name__, frames=[dict(file=Path(f.filename).name, line=f.lineno, function=f.name) for f in traceback.extract_tb(error.__traceback__)])
                        (directory / 'diagnostico.json').write_text(json.dumps(diagnostic), encoding='utf-8')
                    self.queue.update(task['id'], status='failed', stage=str(error) if isinstance(error, ValueError) else 'Falha local inesperada.')
        except Exception as error:
            for task in self.queue.all():
                if task['status'] not in {'done', 'failed'}:
                    self.queue.update(task['id'], stage=str(error) if isinstance(error, ValueError) else 'Não foi possível conectar ao servidor. Confira a rede e tente retomar.')
        finally:
            if server: server.close()
