"""Small durable queue, intentionally operated by one service replica."""
import json
import secrets
import sqlite3
import time
import hashlib
from contextlib import contextmanager

class JobStore:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "jobs.sqlite3"

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT, payload TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS local_submissions (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, job_id TEXT NOT NULL)")
            # A stopped task may already have incurred API charges: never auto-retry it.
            for row in connection.execute("SELECT id,payload FROM jobs WHERE status='running'").fetchall():
                data = json.loads(row["payload"])
                data.update(status="failed", updated=time.time(), stage="Interrompido", error="O servidor reiniciou durante a tarefa. Crie uma nova transcrição para tentar novamente; partes anteriores podem ter sido cobradas.")
                connection.execute("UPDATE jobs SET status='failed',updated=?,payload=? WHERE id=?", (time.time(), json.dumps(data), row["id"]))

    def create(self, data, limit):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= limit:
                raise ValueError("A fila está cheia. Aguarde uma transcrição terminar.")
            identifier, now = secrets.token_hex(16), time.time()
            data = dict(data, id=identifier, status="queued", stage="Na fila", progress=0, created=now, updated=now, error=None)
            connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (identifier, now, now, "queued", json.dumps(data)))
            return data

    def get(self, identifier):
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def local_submission(self, request_id):
        with self.connect() as connection:
            row = connection.execute('SELECT job_id FROM local_submissions WHERE request_id=?', (request_id,)).fetchone()
        return (True, self.get(row[0])) if row else (False, None)

    def create_local(self, data, limit, request_id, fingerprint, result=None):
        """Reserve one server job per desktop submission, including uncertain uploads."""
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT fingerprint,job_id FROM local_submissions WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('Este identificador já foi usado para outro conteúdo.')
                row = connection.execute('SELECT payload FROM jobs WHERE id=?', (old['job_id'],)).fetchone()
                if not row:
                    raise LookupError('O resultado desta solicitação expirou. Ela não será reenviada automaticamente.')
                return json.loads(row[0]), False
            if result is None and connection.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= limit:
                raise OverflowError('A fila está cheia. Aguarde uma transcrição terminar.')
            identifier = hashlib.sha256(('desktop:' + request_id).encode()).hexdigest()[:32]
            now = time.time()
            job = dict(data, id=identifier, local_request_id=request_id, created=now, updated=now, error=None,
                       status='done' if result is not None else 'queued',
                       stage='Transcrição pronta' if result is not None else 'Na fila', progress=100 if result is not None else 0)
            if result is not None:
                job['result'] = result
            connection.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (identifier, now, now, job['status'], json.dumps(job)))
            connection.execute('INSERT INTO local_submissions VALUES (?,?,?)', (request_id, fingerprint, identifier))
            return job, True

    def update(self, identifier, **changes):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                return
            data = json.loads(row[0])
            data.update(changes, updated=time.time())
            connection.execute("UPDATE jobs SET status=?,updated=?,payload=? WHERE id=?", (data["status"], time.time(), json.dumps(data), identifier))

    def next(self):
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def cleanup(self, retention):
        with self.connect() as connection:
            connection.execute("DELETE FROM jobs WHERE status IN ('done','failed') AND updated < ?", (time.time() - retention,))

    def queued_uploads(self):
        with self.connect() as connection:
            rows = connection.execute("SELECT payload FROM jobs WHERE status='queued'").fetchall()
        return {data['upload_id'] for row in rows if (data := json.loads(row[0])).get('kind') == 'upload'}
