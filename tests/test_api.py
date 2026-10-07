import time
import csv
import io

from fastapi.testclient import TestClient

from engineering.store import Store
from engineering.jobs import Jobs
from engineering.contracts import CONTRACTS
import server.main as server


def isolated_client(tmp_path,monkeypatch):
    store=Store(tmp_path)
    monkeypatch.setattr(server,'store',store)
    monkeypatch.setattr(server,'jobs',Jobs(store,database='olist_test_unused'))
    return TestClient(server.app),store


def test_upload_nine_csvs_and_validate_without_touching_mysql(tmp_path,monkeypatch):
    client,store=isolated_client(tmp_path,monkeypatch)
    names=[s.filename for s in CONTRACTS.values()]
    result=client.post('/api/imports/upload',json={'filenames':names,'label':'上传测试'})
    assert result.status_code==200
    batch=result.json()['id']
    for spec in CONTRACTS.values():
        buffer=io.StringIO(newline='');csv.writer(buffer).writerow(spec.columns)
        assert client.put(f'/api/imports/{batch}/files/{spec.filename}',content=buffer.getvalue().encode('utf-8')).status_code==200
    assert client.post(f'/api/imports/{batch}/validate').status_code==200
    for _ in range(100):
        row=store.get(batch)
        if row['status'] in ('ready','invalid','failed'):break
        time.sleep(.02)
    assert row['status']=='ready',row
    assert row['profile']['valid']
    assert client.put(f'/api/imports/{batch}/files/olist_orders_dataset.csv',content=b'changed').status_code==400


def test_upload_and_execute_guards(tmp_path,monkeypatch):
    client,store=isolated_client(tmp_path,monkeypatch)
    assert client.get('/api/health').json()=={'status':'ok','app_version':'3.0'}
    assert client.post('/api/imports/upload',json={'filenames':['../bad.csv']}).status_code==400
    row=store.create(tmp_path)
    assert client.post(f'/api/imports/{row["id"]}/execute').status_code==400
    assert client.get('/api/imports/missing').status_code==404
    assert client.get('/api/exports/raw_orders').status_code==400
    assert client.get('/api/chat').status_code==404
    names=[s.filename for s in CONTRACTS.values()]
    assert client.post('/api/imports/upload',json={'filenames':names,'merge_mode':'upsert'}).status_code==400
    assert client.post('/api/imports/upload',json={'filenames':names,'merge_mode':'delete'}).status_code==400
