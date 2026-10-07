import csv
import json
from pathlib import Path

import pytest

from engineering.contracts import CONTRACTS
from engineering.pipeline import split_sql
from engineering.source import normalize, profile, records


def empty_bundle(tmp_path):
    for spec in CONTRACTS.values():
        with (tmp_path/spec.filename).open('w',encoding='utf-8',newline='') as file:
            csv.writer(file).writerow(spec.columns)


def test_csv_quotes_backslash_embedded_newline_and_utf8(tmp_path):
    empty_bundle(tmp_path)
    spec=CONTRACTS['raw_order_reviews']
    values=['a'*32,'b'*32,'3','avaliação "ótima",','primeira linha\nsegunda linha\\','2018-01-01 00:00:00','2018-01-02 12:00:00']
    with (tmp_path/spec.filename).open('a',encoding='utf-8',newline='') as file:
        csv.writer(file).writerow(values)
    result=profile(tmp_path,tmp_path/'records.sqlite3')
    assert result['valid'],result['errors']
    assert list(records(tmp_path/'records.sqlite3','raw_order_reviews'))[0][0][4]==values[4]
    assert next(x for x in result['files'] if x['table']=='raw_order_reviews')['rows']==1


def test_business_key_conflict_is_not_silently_overwritten(tmp_path):
    empty_bundle(tmp_path)
    spec=CONTRACTS['raw_customers']
    with (tmp_path/spec.filename).open('a',encoding='utf-8',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['a'*32,'b'*32,'9790','são paulo','SP'])
        writer.writerow(['a'*32,'c'*32,'9790','são paulo','SP'])
    result=profile(tmp_path,tmp_path/'records.sqlite3')
    assert not result['valid']
    assert '冲突' in result['errors'][0]['reason']


def test_multiset_preserves_repeated_source_rows(tmp_path):
    empty_bundle(tmp_path)
    spec=CONTRACTS['raw_geolocation']
    with (tmp_path/spec.filename).open('a',encoding='utf-8',newline='') as file:
        csv.writer(file).writerows([['09790','-23.123','-46.123','são paulo','SP']]*3)
    result=profile(tmp_path,tmp_path/'records.sqlite3')
    assert result['valid']
    assert list(records(tmp_path/'records.sqlite3','raw_geolocation'))[0][1]==3


@pytest.mark.parametrize('column,value',[('review_score','6'),('price','-1'),('price','1.001'),('geolocation_lat','NaN'),('geolocation_lng','181'),('customer_id','invalid'),('order_approved_at','2018/01/01'),('customer_state','XX')])
def test_invalid_values(column,value):
    with pytest.raises(ValueError):
        normalize(column,value,False)


def test_zip_and_nullable_dates():
    assert normalize('customer_zip_code_prefix','9790',False)=='09790'
    assert normalize('order_approved_at','',True) is None


def test_sql_splitter_does_not_execute_source_use_statement():
    from engineering.config import ROOT
    statements=list(split_sql((ROOT/'sql'/'06_create_mart_tables.sql').read_text(encoding='utf-8')))
    assert any(s.startswith('INSERT INTO mart_order_delivery') for s in statements)
    assert len(list(split_sql("-- comment;\n SELECT 'a;b'; SELECT 2;")))==2


def test_default_build_notification_accepts_phase_metadata():
    from inspect import signature
    from engineering.pipeline import build
    callback=signature(build).parameters['notify'].default
    assert callback('合并 Raw',5,candidate='_olist_work_test',backup='_olist_backup_test') is None
