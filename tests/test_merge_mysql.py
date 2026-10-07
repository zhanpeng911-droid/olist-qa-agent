"""Targeted MySQL contract tests. Use OLIST_TEST_MYSQL=1; never use DB_NAME."""
import csv
import os
import uuid
from pathlib import Path

import pytest

from engineering.config import DATASET,connect,ident
from engineering.contracts import CONTRACTS
from engineering.pipeline import merge_keyed,merge_multiset,qcolumns,run_script,scalar
from engineering.quality import raw_checks

pytestmark=pytest.mark.skipif(os.getenv('OLIST_TEST_MYSQL')!='1',reason='Set OLIST_TEST_MYSQL=1 for isolated MySQL tests')


@pytest.fixture
def database():
    name='olist_contract_'+uuid.uuid4().hex[:8]
    with connect() as control,control.cursor() as cur:
        cur.execute(f'CREATE DATABASE {ident(name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
    with connect(name) as conn:
        run_script(conn,'01_create_database_and_tables.sql')
        yield conn
    # Intentionally retain only the small isolated test schema for inspection.


def source_row(table):
    with (Path(DATASET)/CONTRACTS[table].filename).open(encoding='utf-8-sig',newline='') as file:
        row=next(csv.DictReader(file))
    return [None if row[c]=='' else row[c] for c in CONTRACTS[table].columns]


def insert(cur,name,spec,rows):
    cur.executemany(f'INSERT INTO {ident(name)} ({qcolumns(spec.columns)}) VALUES ({",".join(["%s"]*len(spec.columns))})',rows)


def test_order_progression_and_null_timestamp_retention(database):
    spec=CONTRACTS['raw_orders'];row=source_row('raw_orders')
    row[spec.columns.index('order_status')]='shipped'
    with database.cursor() as cur:
        insert(cur,'raw_orders',spec,[row])
        cur.execute('CREATE TABLE incoming LIKE raw_orders')
        update=row.copy();update[spec.columns.index('order_status')]='delivered'
        update[spec.columns.index('order_approved_at')]=None
        insert(cur,'incoming',spec,[update])
        result=merge_keyed(cur,'raw_orders',spec,'incoming')
        assert result['updated']==1
        cur.execute('SELECT * FROM raw_orders');actual=cur.fetchone()
        assert actual['order_status']=='delivered'
        assert str(actual['order_approved_at'])==row[spec.columns.index('order_approved_at')]


def test_stale_status_is_rejected(database):
    spec=CONTRACTS['raw_orders'];row=source_row('raw_orders');row[2]='delivered'
    with database.cursor() as cur:
        insert(cur,'raw_orders',spec,[row]);cur.execute('CREATE TABLE incoming LIKE raw_orders')
        row[2]='shipped';insert(cur,'incoming',spec,[row])
        with pytest.raises(ValueError,match='回退'):merge_keyed(cur,'raw_orders',spec,'incoming')
        assert scalar(cur,'SELECT order_status FROM raw_orders')=='delivered'


def test_multiset_overlap_retains_legitimate_duplicates(database):
    table='raw_geolocation';spec=CONTRACTS[table];row=source_row(table)
    with database.cursor() as cur:
        cur.execute(f'ALTER TABLE {ident(table)} ADD COLUMN _eng_hash BINARY(32) GENERATED ALWAYS AS (UNHEX(SHA2(CAST(JSON_ARRAY({qcolumns(spec.columns)}) AS CHAR CHARACTER SET utf8mb4),256))) STORED, ADD KEY idx_eng_hash(_eng_hash)')
        insert(cur,table,spec,[row,row])
        cur.execute(f'CREATE TABLE incoming LIKE {ident(table)}')
        insert(cur,'incoming',spec,[row,row,row])
        result=merge_multiset(cur,table,spec,'incoming')
        assert result['inserted']==1 and result['unchanged']==2
        assert scalar(cur,f'SELECT COUNT(*) FROM {ident(table)}')==3


def test_null_delta_does_not_report_a_false_update(database):
    spec=CONTRACTS['raw_orders'];row=source_row('raw_orders')
    with database.cursor() as cur:
        insert(cur,'raw_orders',spec,[row]);cur.execute('CREATE TABLE incoming LIKE raw_orders')
        row[spec.columns.index('order_approved_at')]=None
        insert(cur,'incoming',spec,[row])
        result=merge_keyed(cur,'raw_orders',spec,'incoming')
        assert result['updated']==0 and result['unchanged']==1


def test_review_text_round_trip_and_overlap(database):
    table='raw_order_reviews';spec=CONTRACTS[table];row=source_row(table)
    row[spec.columns.index('review_comment_title')]='avaliação "ótima",'
    row[spec.columns.index('review_comment_message')]="primeira linha\nsegunda linha\\ d'água"
    with database.cursor() as cur:
        cur.execute(f'ALTER TABLE {ident(table)} ADD COLUMN _eng_hash BINARY(32) GENERATED ALWAYS AS (UNHEX(SHA2(CAST(JSON_ARRAY({qcolumns(spec.columns)}) AS CHAR CHARACTER SET utf8mb4),256))) STORED, ADD KEY idx_eng_hash(_eng_hash)')
        insert(cur,table,spec,[row]);cur.execute(f'CREATE TABLE incoming LIKE {ident(table)}')
        insert(cur,'incoming',spec,[row])
        result=merge_multiset(cur,table,spec,'incoming')
        assert result['inserted']==0 and result['unchanged']==1
        cur.execute('SELECT review_comment_title,review_comment_message FROM raw_order_reviews')
        actual=cur.fetchone()
        assert actual['review_comment_title']==row[3]
        assert actual['review_comment_message']==row[4]


def test_empty_existing_multiset_removes_merge_helper(database):
    table='raw_geolocation';spec=CONTRACTS[table];row=source_row(table)
    with database.cursor() as cur:
        cur.execute(f'ALTER TABLE {ident(table)} ADD COLUMN _eng_hash BINARY(32) GENERATED ALWAYS AS (UNHEX(SHA2(CAST(JSON_ARRAY({qcolumns(spec.columns)}) AS CHAR CHARACTER SET utf8mb4),256))) STORED, ADD KEY idx_eng_hash(_eng_hash)')
        cur.execute(f'CREATE TABLE incoming LIKE {ident(table)}');insert(cur,'incoming',spec,[row,row])
        result=merge_multiset(cur,table,spec,'incoming')
        assert result['inserted']==2
        cur.execute('SHOW COLUMNS FROM raw_geolocation LIKE %s',('_eng_hash',))
        assert cur.fetchone() is None


def test_empty_delta_retains_multiset_without_fingerprinting(database):
    table='raw_geolocation';spec=CONTRACTS[table];row=source_row(table)
    with database.cursor() as cur:
        insert(cur,table,spec,[row,row]);cur.execute(f'CREATE TABLE incoming LIKE {ident(table)}')
        result=merge_multiset(cur,table,spec,'incoming')
        assert result['inserted']==0 and result['unchanged']==0
        assert scalar(cur,f'SELECT COUNT(*) FROM {ident(table)}')==2


@pytest.mark.parametrize('old,new',[('processing','created'),('invoiced','approved'),('canceled','delivered'),('unavailable','canceled')])
def test_all_lifecycle_regressions_are_blocked(database,old,new):
    spec=CONTRACTS['raw_orders'];row=source_row('raw_orders');row[2]=old
    with database.cursor() as cur:
        insert(cur,'raw_orders',spec,[row]);cur.execute('CREATE TABLE incoming LIKE raw_orders')
        row[2]=new;insert(cur,'incoming',spec,[row])
        with pytest.raises(ValueError,match='回退'):merge_keyed(cur,'raw_orders',spec,'incoming')
        assert scalar(cur,'SELECT order_status FROM raw_orders')==old


def test_append_does_not_overwrite_product(database):
    spec=CONTRACTS['raw_products'];row=source_row('raw_products')
    with database.cursor() as cur:
        insert(cur,'raw_products',spec,[row]);cur.execute('CREATE TABLE incoming LIKE raw_products')
        original=row[spec.columns.index('product_weight_g')];row[spec.columns.index('product_weight_g')]=int(original)+1
        insert(cur,'incoming',spec,[row])
        with pytest.raises(ValueError,match='仅追加'):merge_keyed(cur,'raw_products',spec,'incoming','append_only')
        assert scalar(cur,'SELECT product_weight_g FROM raw_products')==int(original)


def test_delivered_without_items_is_fail(database):
    spec=CONTRACTS['raw_orders'];row=source_row('raw_orders');row[2]='delivered'
    with database.cursor() as cur: insert(cur,'raw_orders',spec,[row])
    result=next(c for c in raw_checks(database) if c['name']=='已送达订单商品项完整性')
    assert result['status']=='FAIL' and result['actual']==1
