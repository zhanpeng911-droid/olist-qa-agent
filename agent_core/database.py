from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pymysql

from engineering.config import connect, ident
from engineering.contracts import TABLES
from agent_core import sql_guard


def clean(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    return value


def catalog(database):
    # Metadata only; never enumerate other schemas or credentials.
    with connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY,COLUMN_COMMENT '
                    'FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,ORDINAL_POSITION', (database,))
        rows = cur.fetchall()
    tables = {}
    for row in rows:
        if row['TABLE_NAME'] in TABLES:
            tables.setdefault(row['TABLE_NAME'], []).append({
                'name': row['COLUMN_NAME'], 'type': row['COLUMN_TYPE'],
                'nullable': row['IS_NULLABLE'] == 'YES', 'key': row['COLUMN_KEY'], 'description': row['COLUMN_COMMENT'],
            })
    return {'database': database, 'tables': tables}


def version_token(connection, database):
    with connection.cursor() as cur:
        cur.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s', (database,))
        names = {r['TABLE_NAME'] for r in cur.fetchall()}
        checks = []
        for table in (*TABLES, '_eng_version'):
            if table not in names:
                continue
            cur.execute(f'CHECKSUM TABLE {ident(database)}.{ident(table)}')
            value = cur.fetchone()['Checksum']
            if value is None:
                raise ValueError('无法核验正式库版本，停止候选发布')
            checks.append((table, value))
    return hashlib.sha256(json.dumps(checks).encode()).hexdigest()


def require_compatible(connection):
    """Model SQL must keep the report contract, primary grain and InnoDB engine."""
    import sqlglot
    from sqlglot import exp
    from engineering.config import ROOT
    from engineering.pipeline import split_sql
    expected = {}
    for filename in ('04_create_staging_tables.sql', '06_create_mart_tables.sql', '11_create_operating_item_mart.sql'):
        for statement in split_sql((ROOT/'sql'/filename).read_text(encoding='utf-8-sig')):
            tree = sqlglot.parse_one(statement, read='mysql')
            if isinstance(tree, exp.Create) and isinstance(tree.this, exp.Schema):
                expected[tree.this.this.name] = {c.name for c in tree.this.expressions if isinstance(c, exp.ColumnDef)}
    with connection.cursor() as cur:
        cur.execute('SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')
        engines = {r['TABLE_NAME']: r['ENGINE'] for r in cur.fetchall()}
        for table, columns in expected.items():
            if engines.get(table) != 'InnoDB':
                raise ValueError('候选表缺失或不是InnoDB：' + table)
            cur.execute(f'SHOW COLUMNS FROM {ident(table)}')
            actual = {r['Field'] for r in cur.fetchall()}
            if not columns <= actual:
                raise ValueError('缺少报表兼容字段：' + table + '.' + ','.join(sorted(columns - actual)))
            cur.execute(f'SHOW INDEX FROM {ident(table)} WHERE Key_name=%s', ('PRIMARY',))
            primary = [r['Column_name'] for r in sorted(cur.fetchall(), key=lambda r: r['Seq_in_index'])]
            key = ['zip_code_prefix'] if table == 'stg_geolocation_zip' else (
                ['order_id', 'seller_id'] if table == 'mart_order_seller_delivery' else (
                    ['order_id', 'order_item_id'] if table == 'mart_order_item_business' else ['order_id']))
            if primary != key:
                raise ValueError('候选表主键粒度不兼容：' + table)


def watchdog(connection, seconds):
    thread_id = connection.thread_id()
    def cancel():
        try:
            with connect() as control, control.cursor() as cur:
                cur.execute(f'KILL QUERY {int(thread_id)}')
        except pymysql.MySQLError:
            pass
    timer = threading.Timer(seconds, cancel)
    timer.daemon = True
    timer.start()
    return timer


def query(database, sql, directory: Path, *, max_rows=5000, use_query_account=True):
    original, executed, maximum = sql_guard.select(sql, database, max_rows=max_rows)
    directory.mkdir(parents=True, exist_ok=True)
    # READ ONLY transaction is independent of credentials' broader ETL permissions.
    with connect(database, stream=True, read_only=use_query_account) as conn, conn.cursor() as cur:
        cur.execute('SET SESSION max_execution_time=20000')
        cur.execute('START TRANSACTION READ ONLY')
        timer = watchdog(conn, 25)
        try:
            cur.execute('EXPLAIN FORMAT=JSON ' + executed)
            explain = json.loads(cur.fetchone()['EXPLAIN'])
            cur.fetchall()  # EXPLAIN has one row; consume EOF before the next query.
            def cost(node):
                if isinstance(node, dict):
                    return max([float(node.get('rows_produced_per_join', 0)), *[cost(v) for v in node.values()]])
                if isinstance(node, list):
                    return max([0, *[cost(v) for v in node]])
                return 0
            if cost(explain) > 10_000_000:
                raise ValueError('执行计划预计中间结果过大，请限定时间范围或先聚合再连接')
            cur.execute(executed)
            columns = [c[0] for c in cur.description]
            if len(columns) != len(set(columns)) or len(columns) > 100:
                raise ValueError('输出列需使用唯一别名，且不超过100列')
            preview, count, truncated = [], 0, False
            path = directory / 'result.csv'
            with path.open('w', encoding='utf-8-sig', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(columns)
                for row in cur:
                    if count == maximum:
                        truncated = True
                        continue
                    values = clean(row)
                    # CSV spreadsheet formula injection mitigation (display keeps original values).
                    writer.writerow([("'" + str(values[c])) if isinstance(values[c], str) and values[c].lstrip().startswith(('=', '+', '-', '@')) else values[c] for c in columns])
                    if count < 50:
                        preview.append(values)
                    count += 1
            (directory / 'query.sql').write_text(original + ';\n', encoding='utf-8')
            return {'sql': original, 'executed_sql': executed, 'columns': columns,
                    'rows': preview, 'row_count': count, 'truncated': truncated,
                    'row_limit': maximum, 'explain': explain}
        finally:
            timer.cancel()
            conn.rollback()


def execute_candidate(database, sql):
    if not re.fullmatch(r'_olist_work_[0-9a-f]{16}', database):
        raise ValueError('工程SQL仅可执行于本应用创建的候选库')
    normalized, table = sql_guard.candidate(sql, database)
    with connect(database) as conn, conn.cursor() as cur:
        timer = watchdog(conn, 90)
        try:
            cur.execute(normalized)
            return {'table': table, 'affected_rows': cur.rowcount, 'sql': normalized}
        finally:
            timer.cancel()


def full_csv(database, sql, directory, notify=lambda **_: None, *, max_rows=2_000_000,
             max_bytes=1024**3, seconds=300):
    """Stream original SELECT into a complete-or-failed local artifact.

    Never silently truncate, never buffer rows in pandas, never return data rows
    to the model. The original query's LIMIT remains meaningful.
    """
    original,executed,_=sql_guard.select(sql,database,full_export=True)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    partial=directory/'result.csv.part';finished=directory/'result.csv'
    started=time.monotonic();count=0
    with connect(database,stream=True,read_only=True) as conn,conn.cursor() as cur:
        cur.execute(f'SET SESSION max_execution_time={int(seconds*1000)}')
        cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        timer=watchdog(conn,seconds)
        try:
            cur.execute('EXPLAIN FORMAT=JSON '+executed)
            explain=json.loads(cur.fetchone()['EXPLAIN']);cur.fetchall()
            def cost(node):
                if isinstance(node,dict):return max([float(node.get('rows_produced_per_join',0)),*[cost(v) for v in node.values()]])
                if isinstance(node,list):return max([0,*[cost(v) for v in node]])
                return 0
            if cost(explain)>10_000_000:
                raise ValueError('执行计划预计中间结果过大，请先聚合或限定范围')
            cur.execute(executed)
            columns=[c[0] for c in cur.description]
            if len(columns)!=len(set(columns)) or len(columns)>100:
                raise ValueError('输出列需使用唯一别名，且不超过100列')
            with partial.open('w',encoding='utf-8-sig',newline='') as file:
                writer=csv.writer(file);writer.writerow(columns)
                for row in cur:
                    if count>=max_rows or time.monotonic()-started>=seconds:
                        raise ValueError('完整导出超过行数或时间预算；未提供不完整CSV，请限定范围重试')
                    values=[]
                    for column in columns:
                        value=row[column]
                        if isinstance(value,(date,datetime)):value=value.isoformat()
                        if isinstance(value,bytes):value=value.hex()
                        if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):value="'"+value
                        values.append(value)  # Decimal preserved as exact text.
                    writer.writerow(values);count+=1
                    if count%2000==0:
                        file.flush()
                        if partial.stat().st_size>max_bytes:
                            raise ValueError('完整导出超过文件大小预算；未提供不完整CSV')
                        notify(row_count=count)
                file.flush()
            if partial.stat().st_size>max_bytes or time.monotonic()-started>=seconds:
                raise ValueError('完整导出超过文件大小或时间预算；未提供不完整CSV')
            partial.replace(finished)
            (directory/'query.sql').write_text(original+';\n',encoding='utf-8')
            digest=hashlib.sha256()
            with finished.open('rb') as file:
                for block in iter(lambda:file.read(1024*1024),b''):digest.update(block)
            return {'row_count':count,'columns':columns,'bytes':finished.stat().st_size,
                    'sha256':digest.hexdigest(),'sql':original,'complete':True,'truncated':False}
        except Exception:
            # Generated temporary output only; a failed job has no downloadable artifact.
            if partial.exists():partial.unlink()
            if finished.exists():finished.unlink()
            conn.close()  # Do not drain a rejected unbuffered multi-million-row result.
            raise
        finally:
            timer.cancel()
            if conn.open:conn.rollback()
