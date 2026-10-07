from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from engineering.contracts import CONTRACTS, INTS, MONEY, ORDER_STATUSES, PAYMENTS, STATES

csv.field_size_limit(4 * 1024 * 1024)


def normalize(column, value, nullable):
    if value == '':
        if nullable:
            return None
        raise ValueError(f'{column} 为必填字段')
    if column in INTS:
        if not re.fullmatch(r'\d+', value):
            raise ValueError(f'{column} 必须为非负整数')
        number = int(value)
        if number > 2147483647 or (column in ('order_item_id', 'payment_sequential') and number < 1):
            raise ValueError(f'{column} 超出有效范围')
        if column == 'review_score' and not 1 <= number <= 5:
            raise ValueError('review_score 必须在 1–5 内')
        return number
    if column in MONEY or column in ('geolocation_lat', 'geolocation_lng'):
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError(f'{column} 不是有效数值') from None
        if not number.is_finite():
            raise ValueError(f'{column} 不可为 NaN 或无穷')
        scale = 2 if column in MONEY else 20
        if number != number.quantize(Decimal(10) ** -scale):
            raise ValueError(f'{column} 小数位超过 {scale} 位')
        if column in MONEY and not 0 <= number < Decimal('10000000000'):
            raise ValueError(f'{column} 金额越界')
        if column == 'geolocation_lat' and not -90 <= number <= 90:
            raise ValueError('纬度越界')
        if column == 'geolocation_lng' and not -180 <= number <= 180:
            raise ValueError('经度越界')
        return format(number, f'.{scale}f')
    if column.endswith('_timestamp') or column.endswith('_date') or column == 'order_approved_at':
        try:
            return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            raise ValueError(f'{column} 需要 YYYY-MM-DD HH:MM:SS') from None
    if column.endswith('_zip_code_prefix'):
        if not re.fullmatch(r'\d{1,5}', value):
            raise ValueError(f'{column} 需要 1–5 位数字')
        return value.zfill(5)
    if column.endswith('_id'):
        if not re.fullmatch(r'[0-9a-fA-F]{32}', value):
            raise ValueError(f'{column} 需要 32 位十六进制标识')
        return value.lower()
    if column.endswith('_state') and value not in STATES:
        raise ValueError(f'{column} 不是有效巴西州代码')
    if column == 'order_status' and value not in ORDER_STATUSES:
        raise ValueError('未知订单状态')
    if column == 'payment_type' and value not in PAYMENTS:
        raise ValueError('未知支付方式')
    limit = 65535 if column.startswith('review_comment_') else (120 if column.endswith('_city') else 100)
    if len(value.encode('utf-8')) > limit if column.startswith('review_comment_') else len(value) > limit:
        raise ValueError(f'{column} 超出数据库字段长度')
    return value


def profile(directory: Path, spool: Path, progress=lambda *_: None) -> dict:
    """Parse logical CSV rows; disk-backed keys keep memory independent of file size."""
    missing = [s.filename for s in CONTRACTS.values() if not (directory / s.filename).is_file()]
    if missing:
        raise ValueError('缺少源文件：' + '、'.join(missing))
    result = {'files': [], 'errors': [], 'valid': True}
    with sqlite3.connect(spool) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS records (t TEXT,k TEXT,v TEXT,n INTEGER,PRIMARY KEY(t,k))')
        db.execute('DELETE FROM records')
        for table, spec in CONTRACTS.items():
            progress(f'校验 {spec.filename}', len(result['files']) / 9 * 100)
            path = directory / spec.filename
            digest = hashlib.sha256()
            with path.open('rb') as binary:
                for chunk in iter(lambda: binary.read(1024 * 1024), b''):
                    digest.update(chunk)
            info = {'table': table, 'filename': spec.filename, 'rows': 0, 'duplicates': 0,
                    'conflicts': 0, 'invalid_rows': 0, 'sha256': digest.hexdigest(), 'min_date': None, 'max_date': None}
            try:
                with path.open('r', encoding='utf-8-sig', newline='') as file:
                    reader = csv.reader(file, strict=True)
                    headers = next(reader, [])
                    if len(headers) != len(set(headers)) or set(headers) != set(spec.columns):
                        raise ValueError('列名不匹配；预期：' + ', '.join(spec.columns))
                    positions = [headers.index(c) for c in spec.columns]
                    for logical_row, raw in enumerate(reader, 2):
                        info['rows'] += 1
                        try:
                            if len(raw) != len(headers):
                                raise ValueError(f'字段数应为 {len(headers)}，实际 {len(raw)}')
                            values = [normalize(c, raw[p], c in spec.nullable) for c, p in zip(spec.columns, positions)]
                            canonical = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
                            key = json.dumps([values[spec.columns.index(c)] for c in spec.keys]) if spec.keys else hashlib.sha256(canonical.encode()).hexdigest()
                            old = db.execute('SELECT v FROM records WHERE t=? AND k=?', (table, key)).fetchone()
                            if old:
                                if old[0] != canonical:
                                    info['conflicts'] += 1
                                    raise ValueError('同一业务键出现冲突记录：' + key)
                                info['duplicates'] += 1
                                db.execute('UPDATE records SET n=n+1 WHERE t=? AND k=?', (table, key))
                            else:
                                db.execute('INSERT INTO records VALUES (?,?,?,1)', (table, key, canonical))
                            if table == 'raw_orders':
                                value = values[spec.columns.index('order_purchase_timestamp')]
                                info['min_date'] = min(info['min_date'] or value, value)
                                info['max_date'] = max(info['max_date'] or value, value)
                        except (ValueError, InvalidOperation) as error:
                            info['invalid_rows'] += 1
                            if len(result['errors']) < 30:
                                result['errors'].append({'file': spec.filename, 'row': logical_row, 'physical_line': reader.line_num, 'reason': str(error)})
                        if info['rows'] % 10000 == 0:
                            db.commit()
            except (ValueError, UnicodeError, csv.Error) as error:
                info['invalid_rows'] += 1
                result['errors'].append({'file': spec.filename, 'reason': str(error)})
            if info['invalid_rows']:
                result['valid'] = False
            info['distinct_rows'] = db.execute('SELECT COUNT(*) FROM records WHERE t=?', (table,)).fetchone()[0]
            result['files'].append(info)
            db.commit()
    return result


def records(spool: Path, table: str):
    with sqlite3.connect(spool) as db:
        for values, count in db.execute('SELECT v,n FROM records WHERE t=? ORDER BY rowid', (table,)):
            yield json.loads(values), count
