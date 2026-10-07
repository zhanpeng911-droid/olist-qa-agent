"""End-to-end acceptance on real source CSVs, using an isolated MySQL database.

Produces pre-2018 and 2018+ business batches. Never changes DB_NAME.
Includes overlap/idempotence, failure isolation, rollback and report reconciliation.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout,'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from engineering.config import ARTIFACTS,DATASET,connect,ident
from engineering.contracts import CONTRACTS,TABLES
from engineering.pipeline import build,rollback
from engineering.reports import overview,growth
from engineering.source import profile


def log(message):
    print(message,flush=True)


def partition(source:Path,root:Path):
    old,new=root/'before_2018',root/'2018_and_after'
    old.mkdir();new.mkdir()
    order_ids=set()
    with (source/CONTRACTS['raw_orders'].filename).open(encoding='utf-8-sig',newline='') as file:
        for row in csv.DictReader(file):
            if row['order_purchase_timestamp']<'2018-01-01':order_ids.add(row['order_id'])
    for table,spec in CONTRACTS.items():
        with (source/spec.filename).open(encoding='utf-8-sig',newline='') as file, (old/spec.filename).open('w',encoding='utf-8',newline='') as a, (new/spec.filename).open('w',encoding='utf-8',newline='') as b:
            reader=csv.DictReader(file);wa=csv.DictWriter(a,fieldnames=reader.fieldnames);wb=csv.DictWriter(b,fieldnames=reader.fieldnames);wa.writeheader();wb.writeheader()
            for row in reader:
                if 'order_id' in row:
                    (wa if row['order_id'] in order_ids else wb).writerow(row)
                else:
                    # Dimensions have no business timestamps; repeated complete snapshots are allowed.
                    wa.writerow(row);wb.writerow(row)
    return old,new


def counts(database):
    with connect(database) as conn,conn.cursor() as cur:
        out={}
        for table in TABLES:
            cur.execute(f'SELECT COUNT(*) n FROM {ident(table)}');out[table]=cur.fetchone()['n']
        cur.execute('SELECT SUM(product_value) p,SUM(freight_value) f FROM mart_order_delivery')
        out['money']={k:str(v) for k,v in cur.fetchone().items()}
        return out


def execute(database,source,root,label):
    batch=uuid.uuid4().hex[:16]
    spool=root/(batch+'.sqlite3')
    log(f'{label}: parsing real CSVs')
    prof=profile(source,spool,lambda p,_:log(p))
    assert prof['valid'],prof['errors']
    log(f'{label}: building candidate')
    result=build(database,batch,spool,lambda p,_,**kw:log(p),profile=prof)
    return batch,result,prof


def verify_review_text(database, source):
    checked=0
    with connect(database) as conn,conn.cursor() as cur, (source/CONTRACTS['raw_order_reviews'].filename).open(encoding='utf-8-sig',newline='') as file:
        for row in csv.DictReader(file):
            text=row['review_comment_message']
            if text and any(char in text for char in ('\n',',','"','\\')):
                cur.execute('SELECT review_comment_title,review_comment_message FROM raw_order_reviews WHERE review_id=%s AND order_id=%s',(row['review_id'],row['order_id']))
                assert (row['review_comment_title'] or None,text) in [(r['review_comment_title'],r['review_comment_message']) for r in cur.fetchall()]
                checked+=1
                if checked==25:break
    assert checked==25


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',default=DATASET);args=parser.parse_args()
    root=ARTIFACTS/('verification_'+uuid.uuid4().hex[:8]);root.mkdir(parents=True)
    database='olist_verify_'+uuid.uuid4().hex[:8]
    log(f'ISOLATED_DATABASE={database}')
    a,b=partition(Path(args.source),root)
    first,first_result,_=execute(database,a,root,'pre-2018')
    before=counts(database)
    second,second_result,_=execute(database,b,root,'2018 extension')
    full=counts(database)
    assert full['raw_orders']==99441
    assert full['raw_order_reviews']==99224
    assert full['raw_geolocation']==1000163
    assert full['mart_order_item_business']==112650
    assert full['raw_orders']>before['raw_orders']
    third,third_result,prof=execute(database,Path(args.source),root,'overlap full snapshot')
    repeated=counts(database)
    assert full==repeated,('Idempotence failed',full,repeated)
    assert all(r['inserted']==0 and r['updated']==0 for r in third_result['merge'])
    report=overview(database,'2017-01','2018-08')
    comparison=growth(database,'2017-01','2018-08',None,2017,2018,1,8)
    for dimension in ('state','category','seller'):
        assert abs(float(comparison['concentrations'][dimension]['total'])-float(report['summary']['product_value']))<.01
    verify_review_text(database,Path(args.source))
    # A valid orphan fails the Raw quality gate before publication.
    orphan_dir=root/'orphan';orphan_dir.mkdir()
    for table,spec in CONTRACTS.items():
        with (orphan_dir/spec.filename).open('w',encoding='utf-8',newline='') as file:
            writer=csv.writer(file);writer.writerow(spec.columns)
            if table=='raw_order_payments':writer.writerow(['f'*32,1,'credit_card',1,'1.00'])
    bad=profile(orphan_dir,root/'bad.sqlite3');assert bad['valid']
    try:
        build(database,uuid.uuid4().hex[:16],root/'bad.sqlite3')
    except ValueError as error:
        assert '质量门' in str(error)
    else:raise AssertionError('Orphan batch was published')
    assert counts(database)==full
    assert third_result['outcome']=='unchanged' and third_result['backup'] is None
    rollback(database,second,second_result['backup']);assert counts(database)==before
    out={'database':database,'root':str(root),'status':'PASS','assertions':['pre-2018 + 2018 equals full source','25 real review texts round-trip intact','overlap snapshot is idempotent','all layers and money reconcile','report dimensions reconcile','failed batch does not change published data','atomic rollback restores previous data'],
         'before':before,'full':full,'comparison':comparison['comparison'],'batches':[first,second,third]}
    (root/'acceptance.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    log('FULL_DATA_ACCEPTANCE=PASS');log(f'REPORT={root / "acceptance.json"}')
    return 0


if __name__=='__main__':raise SystemExit(main())
