"""Preview-first pruning, exclusively for backups owned by this batch store."""
import hashlib
import json
import re

from engineering.config import connect, ident
from engineering.contracts import TABLES
from engineering.pipeline import existing_tables, scalar


def plan(store, database, keep=3, connection=None):
    if not 2 <= keep <= 30:
        raise ValueError('保留备份数量必须为 2–30')
    if connection is None:
        with connect() as conn:
            return plan(store, database, keep, conn)
    rows = [r for r in store.list() if r.get('database') == database and r.get('backup')
            and r['status'] in ('published', 'rolled_back') and not r.get('backup_pruned')]
    current = None
    if '_eng_version' in existing_tables(connection, database):
        with connection.cursor() as cur:
            cur.execute(f'SELECT batch_id FROM {ident(database)}._eng_version')
            current = cur.fetchone()['batch_id']
    # Always protect the current version's predecessor in addition to recent backups.
    protected = {r['backup'] for r in rows[:keep]} | {r['backup'] for r in rows if r['id'] == current}
    targets = []
    for row in rows:
        name = row['backup']
        if name in protected:
            continue
        if not re.fullmatch(r'_olist_backup_[0-9a-f]{16}', name) or name != '_olist_backup_' + row['id']:
            continue
        tables = existing_tables(connection, name)
        if not tables or not tables <= set(TABLES) | {'_eng_version'}:
            continue
        targets.append({'schema': name, 'batch': row['id'], 'tables': sorted(tables)})
    digest = hashlib.sha256(json.dumps([database, keep, current, targets], sort_keys=True).encode()).hexdigest()
    return {'database': database, 'keep': keep, 'current': current, 'targets': targets,
            'protected': sorted(protected), 'token': digest}


def prune(store, database, keep, token, confirmed_schemas):
    with connect() as conn, conn.cursor() as cur:
        if not scalar(cur, "SELECT GET_LOCK('olist_engineering_publish',0)"):
            raise ValueError('数据任务执行中，不能清理备份')
        try:
            preview = plan(store, database, keep, conn)
            names = [r['schema'] for r in preview['targets']]
            if preview['token'] != token or sorted(names) != sorted(confirmed_schemas):
                raise ValueError('清理计划已变化或确认目标不一致，请重新预览')
            for target in preview['targets']:
                cur.execute(f'DROP DATABASE {ident(target["schema"])}')
                store.update(target['batch'], backup_pruned=True)
            return {'removed': names, 'recoverable': False}
        finally:
            cur.execute("SELECT RELEASE_LOCK('olist_engineering_publish')")
