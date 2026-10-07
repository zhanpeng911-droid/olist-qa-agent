from __future__ import annotations

import shutil
import threading
from pathlib import Path

from engineering.config import DATABASE
from engineering.contracts import CONTRACTS
from engineering.pipeline import build, rollback
from engineering.source import profile
from engineering.store import Store


class Jobs:
    def __init__(self, store: Store, database=DATABASE):
        self.store = store
        self.database = database
        self.lock = threading.Lock()

    def submit(self, function, *args):
        if not self.lock.acquire(blocking=False):
            raise ValueError('当前有数据任务正在执行；请等待完成后重试')
        def worker():
            try:
                function(*args)
            finally:
                self.lock.release()
        threading.Thread(target=worker, daemon=True, name='olist-pipeline').start()

    def prepare(self, batch):
        try:
            row = self.store.get(batch)
            target = self.store.root / batch / 'source'
            target.mkdir(exist_ok=True)
            source = Path(row['source'])
            if source.resolve() != target.resolve():
                self.store.event(batch, '保存不可变源文件快照', 0, status='validating')
                for spec in CONTRACTS.values():
                    if not (source / spec.filename).is_file():
                        raise ValueError('缺少文件：' + spec.filename)
                    shutil.copy2(source / spec.filename, target / spec.filename)
            result = profile(target, self.store.root / batch / 'records.sqlite3',
                             lambda phase, pct: self.store.event(batch, phase, pct, status='validating'))
            self.store.event(batch, '校验通过，可执行构建' if result['valid'] else '源数据校验未通过', 100,
                             status='ready' if result['valid'] else 'invalid', profile=result)
        except Exception as error:
            self.store.event(batch, '校验失败', 100, status='failed', error=str(error))

    def execute(self, batch):
        try:
            result = build(self.database, batch, self.store.root / batch / 'records.sqlite3',
                           lambda phase, pct, **kw: self.store.event(batch, phase, pct, status='building', **kw),
                           mode=self.store.get(batch).get('merge_mode', 'append_only'), snapshot_at=self.store.get(batch).get('snapshot_at'), profile=self.store.get(batch)['profile'])
            status = result.pop('outcome')
            self.store.event(batch, '数据未变化，沿用当前版本，未新增备份' if status == 'unchanged' else '三层数据已原子发布，报表可刷新', 100, status=status, **result)
        except Exception as error:
            self.store.event(batch, '构建失败，已发布数据保持可用', 100, status='failed', error=str(error))

    def recover(self):
        """Distinguish interrupted work from a committed publish after process restart."""
        from engineering.config import connect, ident
        for row in self.store.list():
            if row['status'] not in ('queued', 'validating', 'building'):
                continue
            committed = False
            if row['status'] == 'building':
                try:
                    with connect(self.database) as conn, conn.cursor() as cur:
                        cur.execute('SELECT batch_id FROM _eng_version')
                        committed = cur.fetchone()['batch_id'] == row['id']
                except Exception:
                    pass
            self.store.update(row['id'], status='published' if committed else 'interrupted',
                              phase='已核验发布完成' if committed else '任务因服务退出中断，请重新提交；已发布库未被部分覆盖')
