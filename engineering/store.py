from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'batches.sqlite3'
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def connection(self):
        return sqlite3.connect(self.path, timeout=30)

    def list(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM batches ORDER BY rowid DESC')]

    def get(self, key):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM batches WHERE id=?', (key,)).fetchone()
        if not row:
            raise KeyError('批次不存在')
        return json.loads(row[0])

    def create(self, source, label=''):
        key = uuid.uuid4().hex[:16]
        row = {'id': key, 'label': label or '原始数据导入', 'source': str(source), 'created_at': now(),
               'status': 'queued', 'phase': '等待校验', 'progress': 0, 'events': [], 'profile': None, 'checks': [], 'merge': []}
        (self.root / key).mkdir()
        with self.connection() as db:
            db.execute('INSERT INTO batches VALUES (?,?)', (key, json.dumps(row, ensure_ascii=False)))
        return row

    def update(self, key, **changes):
        with self.lock:
            row = self.get(key)
            row.update(changes, updated_at=now())
            with self.connection() as db:
                db.execute('UPDATE batches SET payload=? WHERE id=?', (json.dumps(row, ensure_ascii=False, default=str), key))
            return row

    def event(self, key, phase, progress, **changes):
        row = self.get(key)
        events = row.get('events', []) + [{'at': now(), 'phase': phase}]
        return self.update(key, phase=phase, progress=round(progress, 1), events=events, **changes)
