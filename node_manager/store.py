import contextlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from cryptography.fernet import Fernet
from .diagnostics import event


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as output:
        os.chmod(temporary, 0o600)
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "manager.sqlite3"
        key = self.root / "credentials.key"
        if not key.exists():
            with key.open("xb") as output:
                os.chmod(key, 0o600)
                output.write(Fernet.generate_key())
        self.cipher = Fernet(key.read_bytes())
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, ip TEXT UNIQUE NOT NULL,
                    request TEXT NOT NULL, secret BLOB, state TEXT NOT NULL,
                    phase TEXT NOT NULL, error TEXT, context TEXT NOT NULL DEFAULT '{}',
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, job TEXT NOT NULL, at REAL NOT NULL, phase TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS nodes (uuid TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS removals (
                    uuid TEXT PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL,
                    state TEXT NOT NULL, phase TEXT NOT NULL, error TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS probes (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, region TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS observations (
                    probe TEXT NOT NULL, node TEXT NOT NULL, sequence INTEGER NOT NULL,
                    received REAL NOT NULL, ok INTEGER NOT NULL, failures INTEGER NOT NULL,
                    latency REAL, error TEXT, PRIMARY KEY(probe,node));
            """)
            for row in db.execute("SELECT request,context,updated FROM jobs WHERE state='removed'").fetchall():
                request, context = json.loads(row['request']), json.loads(row['context'])
                if context.get('node_uuid'):
                    db.execute("INSERT OR IGNORE INTO removals VALUES (?,?,?,'removed','removed',NULL,?,?)",
                               (context['node_uuid'], request['name'], request['ip'], row['updated'], row['updated']))
            db.execute("UPDATE jobs SET phase='removed' WHERE state='removed' AND phase!='removed'")
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_job(self, request, idem):
        public = request.model_dump(exclude={"credentials"})
        encoded = json.dumps(public, sort_keys=True)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM jobs WHERE idem=?", (idem,)).fetchone()
            if old:
                from .models import AddNode
                normalized_old = AddNode.model_validate(json.loads(old['request'])).model_dump(exclude={'credentials'})
                if normalized_old != public:
                    raise ValueError("idempotency_key_conflict")
                return self.public_job(old)
            occupied = db.execute("SELECT state FROM jobs WHERE ip=?", (request.ip,)).fetchone()
            if occupied:
                raise ValueError("retired_address_requires_manual_reset" if occupied['state'] == 'removed'
                                 else "ip_already_enrolled_or_reserved")
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 20:
                raise ValueError("queue_full")
            existing = [json.loads(r[0]) for r in db.execute("SELECT metadata FROM nodes")]
            queued = [json.loads(r[0]) for r in db.execute("SELECT request FROM jobs WHERE state!='removed'")]
            if any(n["name"] == request.name for n in existing + queued):
                raise ValueError("node_name_already_used")
            used = {n["management_ip"] for n in existing}
            used |= {json.loads(r[0]).get("management_ip") for r in db.execute("SELECT context FROM jobs WHERE state!='removed'")}
            address = next((f"10.77.{i // 256}.{i % 256}" for i in range(4, 65535)
                            if f"10.77.{i // 256}.{i % 256}" not in used), None)
            if not address:
                raise ValueError("management_address_pool_exhausted")
            job = str(uuid.uuid4())
            secret = self.cipher.encrypt(json.dumps(request.credentials.reveal()).encode())
            now = time.time()
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,'queued','queued',NULL,?,?,?)",
                       (job, idem, request.ip, encoded, secret, json.dumps({"management_ip": address}), now, now))
            return self.public_job(db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone())

    @staticmethod
    def public_job(row):
        return {k: row[k] for k in ("id", "ip", "state", "phase", "error", "created", "updated")}

    def get(self, job):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None:
                raise KeyError(job)
            result = dict(row)
            result["request"] = json.loads(result["request"])
            result["context"] = json.loads(result["context"])
            return result

    def claim(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (time.time(), row[0]))
                return row[0]

    def phase(self, job, phase, **context):
        event('node_install_phase', job_id=job, phase=phase)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            saved = json.loads(db.execute("SELECT context FROM jobs WHERE id=?", (job,)).fetchone()[0])
            saved.update(context)
            db.execute("UPDATE jobs SET phase=?,context=?,updated=? WHERE id=?",
                       (phase, json.dumps(saved), time.time(), job))
            db.execute("INSERT INTO events(job,at,phase) VALUES (?,?,?)", (job, time.time(), phase))

    def finish(self, job, error=None):
        event('node_install_failed' if error else 'node_install_ready', 'ERROR' if error else 'INFO', job_id=job, error=error)
        with self.db() as db:
            db.execute("UPDATE jobs SET state=?,error=?,secret=NULL,updated=? WHERE id=?",
                       ("failed" if error else "ready", error, time.time(), job))

    def forget_credentials(self, job):
        with self.db() as db:
            db.execute("UPDATE jobs SET secret=NULL WHERE id=?", (job,))

    def pause(self, job):
        with self.db() as db:
            db.execute("UPDATE jobs SET state='waiting_review',error=NULL,secret=NULL,updated=? WHERE id=?",
                       (time.time(), job))

    def retry(self, job, credentials):
        with self.db() as db:
            secret = self.cipher.encrypt(json.dumps(credentials.reveal()).encode())
            changed = db.execute("UPDATE jobs SET state='queued',error=NULL,secret=?,updated=? WHERE id=? AND state='failed'",
                                 (secret, time.time(), job)).rowcount
            if changed != 1:
                raise ValueError("job_is_not_failed")

    def save_node(self, node):
        with self.db() as db:
            db.execute("INSERT INTO nodes VALUES (?,?) ON CONFLICT(uuid) DO UPDATE SET metadata=excluded.metadata",
                       (node["uuid"], json.dumps(node)))

    def nodes(self):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT metadata FROM nodes ORDER BY rowid")]

    def delete_node(self, node):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM observations WHERE node=?", (node['uuid'],))
            db.execute("DELETE FROM nodes WHERE uuid=?", (node['uuid'],))
            db.execute("UPDATE jobs SET state='removed',phase='removed',updated=? WHERE ip=? AND state NOT IN ('queued','running')",
                       (time.time(), node['public_ip']))
            db.execute("UPDATE removals SET state='removed',phase='removed',error=NULL,updated=? WHERE uuid=?",
                       (time.time(), node['uuid']))

    def removal_phase(self, node, phase, state='running', error=None):
        event('node_removal_phase', 'ERROR' if error else 'INFO', node_uuid=node['uuid'], phase=phase, error=error)
        now = time.time()
        with self.db() as db:
            db.execute("INSERT INTO removals VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET "
                       "state=excluded.state,phase=excluded.phase,error=excluded.error,updated=excluded.updated",
                       (node['uuid'], node['name'], node['public_ip'], state, phase, error, now, now))

    def removal(self, node_uuid):
        with self.db() as db:
            row = db.execute("SELECT * FROM removals WHERE uuid=?", (node_uuid,)).fetchone()
            return dict(row) if row else None
