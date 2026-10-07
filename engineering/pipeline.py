from __future__ import annotations

import json
import re
import hashlib
from itertools import chain
from pathlib import Path

from engineering.config import ROOT, connect, ident
from engineering.contracts import CONTRACTS, TABLES
from engineering.source import records
from engineering.policy import STAGES, TERMINAL, transition_allowed, validate_policy


def split_sql(text):
    text = re.sub(r'^\s*--.*$', '', text, flags=re.M)
    quote = None
    start = 0
    escaped = False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == '\\' and quote:
            escaped = True
        elif quote and char == quote:
            quote = None
        elif not quote and char in "'\"`":
            quote = char
        elif not quote and char == ';':
            if text[start:i].strip():
                yield text[start:i].strip()
            start = i + 1
    if text[start:].strip():
        yield text[start:].strip()


def run_script(connection, filename, skip_tables=()):
    text = (ROOT / 'sql' / filename).read_text(encoding='utf-8-sig')
    # Zero-value orders have an undefined freight ratio, represented as NULL.
    text = re.sub(r'(\b(?:seller_|item_)?freight_ratio\s+DECIMAL\([^)]*\)\s+)NOT NULL', r'\1NULL', text)
    with connection.cursor() as cur:
        for statement in split_sql(text):
            if statement.upper().startswith(('USE ', 'CREATE DATABASE', 'SHOW ', 'SELECT ')):
                continue
            target = re.match(r'(?:CREATE TABLE|DROP TABLE(?: IF EXISTS)?|INSERT INTO|ANALYZE TABLE)\s+`?(\w+)', statement, re.I)
            if target and target.group(1) in skip_tables:
                continue
            if not statement.upper().startswith(('CREATE TABLE', 'DROP TABLE', 'INSERT INTO', 'SET ', 'ANALYZE TABLE')):
                raise ValueError(f'构建脚本包含不支持的语句：{statement[:50]}')
            cur.execute(statement)


def existing_tables(connection, database):
    with connection.cursor() as cur:
        cur.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE=%s', (database, 'BASE TABLE'))
        return {r['TABLE_NAME'] for r in cur.fetchall()}


def scalar(cur, sql):
    cur.execute(sql)
    return next(iter(cur.fetchone().values()))


def qcolumns(columns, alias=''):
    return ','.join((alias + '.' if alias else '') + ident(c) for c in columns)


def keyjoin(spec, left='a', right='b'):
    return ' AND '.join(f'{left}.{ident(c)}={right}.{ident(c)}' for c in spec.keys)


def merge_keyed(cur, table, spec, incoming, mode='upsert'):
    join = keyjoin(spec)
    changed = ' OR '.join(
        f'NOT(a.{ident(c)} <=> ' + (f'COALESCE(b.{ident(c)},a.{ident(c)})' if table=='raw_orders' and c in spec.nullable else f'b.{ident(c)}') + ')'
        for c in spec.columns if c not in spec.keys)
    inserted = scalar(cur, f'SELECT COUNT(*) FROM {ident(incoming)} b LEFT JOIN {ident(table)} a ON {join} WHERE a.{ident(spec.keys[0])} IS NULL')
    updated = scalar(cur, f'SELECT COUNT(*) FROM {ident(table)} a JOIN {ident(incoming)} b ON {join} WHERE {changed}')
    unchanged = scalar(cur, f'SELECT COUNT(*) FROM {ident(table)} a JOIN {ident(incoming)} b ON {join} WHERE NOT({changed})')
    if table == 'raw_orders':
        states = set(STAGES) | TERMINAL
        forbidden = ' OR '.join(f"(a.order_status='{a}' AND b.order_status='{b}')" for a in sorted(states) for b in sorted(states) if not transition_allowed(a, b))
        conflict = scalar(cur, f"SELECT COUNT(*) FROM {ident(table)} a JOIN {ident(incoming)} b ON {join} WHERE a.customer_id<>b.customer_id OR a.order_purchase_timestamp<>b.order_purchase_timestamp OR ({forbidden})")
        if conflict:
            raise ValueError(f'{conflict} 笔已有订单出现身份冲突或状态回退；请提供当前快照或更正源数据')
        # Empty timestamps in a delta are not deletion instructions.
        setters = ','.join(f'a.{ident(c)}=' + (f'COALESCE(b.{ident(c)},a.{ident(c)})' if c in spec.nullable else f'b.{ident(c)}') for c in spec.columns if c not in spec.keys)
    else:
        setters = ','.join(f'a.{ident(c)}=b.{ident(c)}' for c in spec.columns if c not in spec.keys)
    if mode == 'append_only' and updated:
        raise ValueError(f'{table} 有 {updated} 条已有业务键发生变化；仅追加模式拒绝覆盖，请核验后提供更新的源快照时间')
    cur.execute(f'UPDATE {ident(table)} a JOIN {ident(incoming)} b ON {join} SET {setters} WHERE {changed}')
    cur.execute(f'INSERT INTO {ident(table)} ({qcolumns(spec.columns)}) SELECT {qcolumns(spec.columns,"b")} FROM {ident(incoming)} b LEFT JOIN {ident(table)} a ON {join} WHERE a.{ident(spec.keys[0])} IS NULL')
    return {'table': table, 'inserted': inserted, 'updated': updated, 'unchanged': unchanged, 'mode': '按业务键合并'}


def merge_multiset(cur, table, spec, incoming):
    """Preserve source multiplicity while making overlapping snapshots idempotent."""
    received = scalar(cur, f'SELECT COUNT(*) FROM {ident(incoming)}')
    if not received:
        return {'table':table,'inserted':0,'updated':0,'unchanged':0,'mode':'本批次无增量记录，保留历史数据'}
    if not scalar(cur, f'SELECT COUNT(*) FROM {ident(table)}'):
        cur.execute(f'INSERT INTO {ident(table)} ({qcolumns(spec.columns)}) SELECT {qcolumns(spec.columns)} FROM {ident(incoming)}')
        added=cur.rowcount
        cur.execute(f'SHOW COLUMNS FROM {ident(table)} LIKE %s',('_eng_hash',))
        if cur.fetchone():
            cur.execute(f'ALTER TABLE {ident(table)} DROP COLUMN _eng_hash')
        return {'table': table, 'inserted':added, 'updated':0, 'unchanged':0, 'mode':'首次接入，保留全部原始记录'}
    counter = '_counts_' + table
    cur.execute(f'CREATE TABLE {ident(counter)} AS SELECT _eng_hash,COUNT(*) n FROM {ident(table)} GROUP BY _eng_hash')
    cur.execute(f'ALTER TABLE {ident(counter)} ADD PRIMARY KEY (_eng_hash)')
    surrogate = 'review_row_id' if table == 'raw_order_reviews' else 'geolocation_row_id'
    cols = qcolumns(spec.columns)
    sql = f'INSERT INTO {ident(table)} ({cols}) SELECT {qcolumns(spec.columns,"b")} FROM (SELECT i.*,ROW_NUMBER() OVER(PARTITION BY _eng_hash ORDER BY {ident(surrogate)}) occurrence FROM {ident(incoming)} i) b LEFT JOIN {ident(counter)} c ON b._eng_hash=c._eng_hash WHERE b.occurrence>COALESCE(c.n,0)'
    cur.execute(sql)
    added = cur.rowcount
    cur.execute(f'DROP TABLE {ident(counter)}')
    # Internal merge keys are discarded; published Raw retains the source contract.
    cur.execute(f'ALTER TABLE {ident(table)} DROP COLUMN _eng_hash')
    return {'table': table, 'inserted': added, 'updated': 0, 'unchanged': received-added, 'mode': '按完整记录匹配，保留重复次数'}


def read_version(connection, database, tables=None):
    if '_eng_version' not in (tables if tables is not None else existing_tables(connection, database)):
        return {}
    with connection.cursor() as cur:
        cur.execute(f'SELECT * FROM {ident(database)}._eng_version')
        row = cur.fetchone() or {}
    return json.loads(row.get('metadata') or '{}')


def build_signature():
    digest = hashlib.sha256()
    for path in sorted([*(ROOT / 'sql').glob('*.sql'), *(ROOT / 'engineering').glob('*.py')]):
        digest.update(path.name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


STAGING_DEPENDENCIES = {
    'stg_geolocation_zip': {'raw_geolocation'},
    'stg_order_payments': {'raw_order_payments'},
    'stg_order_reviews': {'raw_order_reviews'},
    'stg_order_items_summary': {'raw_order_items', 'raw_products', 'raw_category_translation'},
}


def build(database, batch, spool: Path, notify=lambda *_, **__: None, *, mode='append_only', snapshot_at=None, profile=None, stage_only=False):
    candidate = '_olist_work_' + batch
    backup = '_olist_backup_' + batch
    with connect() as control:
        with control.cursor() as cur:
            # MySQL advisory lock also coordinates separate server processes.
            if not scalar(cur, "SELECT GET_LOCK('olist_engineering_publish',0)"):
                raise RuntimeError('另一个数据构建或发布任务正在运行')
        try:
            old_tables = existing_tables(control, database)
            previous = read_version(control, database, old_tables)
            stamp = validate_policy(mode, snapshot_at, previous.get('snapshot_at'))
            signature = build_signature()
            if stage_only:
                from agent_core.database import version_token
                base_token = version_token(control, database)
            hashes = {f['table']: f['sha256'] for f in (profile or {}).get('files', [])}
            with control.cursor() as cur:
                cur.execute(f'CREATE DATABASE {ident(candidate)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            if old_tables and not set(CONTRACTS) <= old_tables:
                raise ValueError('目标库缺少完整 Raw 层，无法安全增量合并；请使用新数据库进行首次构建')
            with connect(candidate) as work:
                run_script(work, '01_create_database_and_tables.sql')
                merge = []
                for index, (table, spec) in enumerate(CONTRACTS.items()):
                    notify(f'合并 {table}', 5 + index / 9 * 35, candidate=candidate, backup=backup)
                    source_records = records(spool, table)
                    first = next(source_records, None)
                    with work.cursor() as cur:
                        same_input = table in old_tables and hashes.get(table) is not None and hashes[table] == previous.get('source_hashes', {}).get(table) and previous.get('build_signature') == signature
                        if not spec.keys and table in old_tables and first is not None and not same_input:
                            # Compute fingerprints during insertion, not in an extra million-row UPDATE.
                            cur.execute(f'ALTER TABLE {ident(table)} ADD COLUMN _eng_hash BINARY(32) GENERATED ALWAYS AS (UNHEX(SHA2(CAST(JSON_ARRAY({qcolumns(spec.columns)}) AS CHAR CHARACTER SET utf8mb4),256))) STORED, ADD KEY idx_eng_hash (_eng_hash)')
                        if table in old_tables:
                            proxy = ('review_row_id',) if table == 'raw_order_reviews' else (('geolocation_row_id',) if table == 'raw_geolocation' else ())
                            columns = (*proxy, *spec.columns)
                            cur.execute(f'INSERT INTO {ident(table)} ({qcolumns(columns)}) SELECT {qcolumns(columns)} FROM {ident(database)}.{ident(table)}')
                        if same_input:
                            merge.append({'table': table, 'inserted': 0, 'updated': 0, 'unchanged': (profile or {}).get('files', [])[index]['rows'], 'total': scalar(cur, f'SELECT COUNT(*) FROM {ident(table)}'), 'mode': '源文件指纹未变，复用 Raw 快照'})
                            notify(f'{table} 复用完成', 5 + (index+1)/9*35, merge=merge)
                            continue
                        incoming = '_incoming_' + table
                        cur.execute(f'CREATE TABLE {ident(incoming)} LIKE {ident(table)}')
                        sql = f'INSERT INTO {ident(incoming)} ({qcolumns(spec.columns)}) VALUES ({",".join(["%s"]*len(spec.columns))})'
                        chunk = []
                        for values, count in chain((first,), source_records) if first is not None else ():
                            for _ in range(1 if spec.keys else count):
                                chunk.append(tuple(values))
                                if len(chunk) == 2000:
                                    cur.executemany(sql, chunk)
                                    chunk.clear()
                        if chunk:
                            cur.executemany(sql, chunk)
                        item = merge_keyed(cur, table, spec, incoming, mode) if spec.keys else merge_multiset(cur, table, spec, incoming)
                        item['total'] = scalar(cur, f'SELECT COUNT(*) FROM {ident(table)}')
                        merge.append(item)
                        cur.execute(f'DROP TABLE {ident(incoming)}')
                    notify(f'{table} 合并完成', 5 + (index+1)/9*35, merge=merge)
                from engineering.quality import raw_checks, model_checks
                checks = raw_checks(work)
                notify('Raw 质量门', 42, checks=checks)
                require_pass(checks)
                if stage_only:
                    # LLM tools build Staging / Mart in this disposable workspace.
                    # No formal-table writes, backups or publication occur here.
                    return {'outcome': 'prepared', 'candidate': candidate, 'backup': backup,
                            'base_token': base_token, 'merge': merge, 'checks': checks}
                changed_tables = {r['table'] for r in merge if r['inserted'] or r['updated']}
                if not changed_tables and previous.get('build_signature') == signature and set(TABLES) <= old_tables and (mode != 'upsert' or stamp == previous.get('snapshot_at')):
                    with connect(database) as old:
                        checks += model_checks(old)
                    require_pass(checks)
                    # This schema was created by this invocation and holds only disposable clones.
                    with control.cursor() as cur:
                        cur.execute(f'DROP DATABASE {ident(candidate)}')
                    return {'outcome': 'unchanged', 'candidate': None, 'backup': None, 'merge': merge, 'checks': checks, 'reused_staging': list(STAGING_DEPENDENCIES)}
                reused = []
                if previous.get('build_signature') == signature:
                    with work.cursor() as cur:
                        for table, dependencies in STAGING_DEPENDENCIES.items():
                            if table in old_tables and not dependencies & changed_tables:
                                cur.execute(f'CREATE TABLE {ident(table)} LIKE {ident(database)}.{ident(table)}')
                                cur.execute(f'INSERT INTO {ident(table)} SELECT * FROM {ident(database)}.{ident(table)}')
                                reused.append(table)
                notify('构建 4 张 Staging 聚合表', 48)
                run_script(work, '04_create_staging_tables.sql', reused)
                notify('构建订单及订单—卖家 Mart', 60)
                run_script(work, '06_create_mart_tables.sql')
                notify('构建商品项 Mart', 76)
                run_script(work, '11_create_operating_item_mart.sql')
                checks += model_checks(work)
                notify('全链路质量门与金额对账', 90, checks=checks)
                require_pass(checks)
                with work.cursor() as cur:
                    meta = {'build_signature': signature, 'source_hashes': hashes, 'snapshot_at': stamp if mode == 'upsert' else previous.get('snapshot_at'), 'merge_mode': mode}
                    cur.execute('CREATE TABLE _eng_version (batch_id VARCHAR(32) PRIMARY KEY,published_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,metadata TEXT NULL) ENGINE=InnoDB')
                    cur.execute('INSERT INTO _eng_version (batch_id,metadata) VALUES (%s,%s)', (batch, json.dumps(meta)))
                publish(control, database, candidate, backup, old_tables)
                return {'outcome': 'published', 'candidate': candidate, 'backup': backup, 'merge': merge, 'checks': checks, 'reused_staging': reused}
        finally:
            with control.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK('olist_engineering_publish')")


def require_pass(checks):
    failed = [r['name'] for r in checks if r['status'] == 'FAIL']
    if failed:
        raise ValueError('质量门未通过：' + '、'.join(failed))


def publish(control, database, candidate, backup, old_tables):
    with control.cursor() as cur:
        cur.execute(f'CREATE DATABASE IF NOT EXISTS {ident(database)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        cur.execute(f'CREATE DATABASE {ident(backup)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        renames = []
        for table in (*TABLES, '_eng_version'):
            if table in old_tables:
                renames.append(f'{ident(database)}.{ident(table)} TO {ident(backup)}.{ident(table)}')
            renames.append(f'{ident(candidate)}.{ident(table)} TO {ident(database)}.{ident(table)}')
        # One MySQL 8 atomic DDL publishes all layers together.
        cur.execute('RENAME TABLE ' + ','.join(renames))


def rollback(database, batch, backup, *, expected_token=None, expected_backup_token=None):
    """Only the latest published batch can be rolled back without losing newer work."""
    retired = '_olist_retired_' + batch
    with connect() as control:
        with control.cursor() as cur:
            if not scalar(cur, "SELECT GET_LOCK('olist_engineering_publish',0)"):
                raise ValueError('有构建任务正在运行')
            try:
                if expected_token is not None:
                    from agent_core.database import version_token
                    if version_token(control,database)!=expected_token:
                        raise ValueError('当前数据已变化，不能按旧预览回滚，请重新核验')
                cur.execute(f'SELECT batch_id FROM {ident(database)}._eng_version')
                if cur.fetchone()['batch_id'] != batch:
                    raise ValueError('仅允许回滚当前发布的最后一个批次')
                old = existing_tables(control, backup)
                if not set(TABLES) <= old:
                    raise ValueError('上一版不包含完整三层表，无法回滚')
                if expected_backup_token is not None:
                    from agent_core.database import version_token
                    if version_token(control,backup)!=expected_backup_token:
                        raise ValueError('备份内容已变化，不能按旧计划恢复')
                cur.execute(f'CREATE DATABASE {ident(retired)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
                moves = []
                for table in (*TABLES, '_eng_version'):
                    moves.append(f'{ident(database)}.{ident(table)} TO {ident(retired)}.{ident(table)}')
                    if table in old:
                        moves.append(f'{ident(backup)}.{ident(table)} TO {ident(database)}.{ident(table)}')
                cur.execute('RENAME TABLE ' + ','.join(moves))
            finally:
                cur.execute("SELECT RELEASE_LOCK('olist_engineering_publish')")
    return retired
