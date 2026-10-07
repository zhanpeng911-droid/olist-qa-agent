"""Only generated empty backup schemas are deleted by this test."""
import os
import uuid
import pytest
from engineering.store import Store
from engineering.config import connect,ident
from engineering.retention import plan,prune
from engineering.pipeline import existing_tables

pytestmark=pytest.mark.skipif(os.getenv('OLIST_TEST_MYSQL')!='1',reason='Set OLIST_TEST_MYSQL=1')


def test_retention_targets_confirmed_owned_backups_only(tmp_path):
    store=Store(tmp_path);database='olist_retention_'+uuid.uuid4().hex[:8];rows=[]
    with connect() as conn,conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE {ident(database)}')
        cur.execute(f'CREATE TABLE {ident(database)}._eng_version(batch_id VARCHAR(32))')
        for _ in range(4):
            row=store.create(tmp_path);backup='_olist_backup_'+row['id']
            cur.execute(f'CREATE DATABASE {ident(backup)}')
            cur.execute(f'CREATE TABLE {ident(backup)}.raw_orders(id INT)')
            store.update(row['id'],database=database,backup=backup,status='published');rows.append(row)
        cur.execute(f'INSERT INTO {ident(database)}._eng_version VALUES(%s)',(rows[-1]['id'],))
    preview=plan(store,database,2)
    assert {t['batch'] for t in preview['targets']}=={r['id'] for r in rows[:2]}
    with pytest.raises(ValueError,match='计划'): prune(store,database,2,preview['token'],[database])
    result=prune(store,database,2,preview['token'],[t['schema'] for t in preview['targets']])
    assert len(result['removed'])==2 and result['recoverable'] is False
    with connect() as conn:
        assert existing_tables(conn,database)=={'_eng_version'}
        assert existing_tables(conn,'_olist_backup_'+rows[-1]['id'])=={'raw_orders'}
    assert all(store.get(r['id'])['backup_pruned'] for r in rows[:2])
