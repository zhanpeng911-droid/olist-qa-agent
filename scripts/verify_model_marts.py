"""Read-only full-cell verification of actual-LLM published test Mart tables."""
import argparse
import json
import re
import uuid
from datetime import datetime, timezone

from run_llm_eval import mart_digests, source_seller_reference, verify_seller_source
from agent_core.database import version_token
from engineering.config import ARTIFACTS, DATABASE, DATASET, connect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--targets',nargs='+',required=True)
    parser.add_argument('--source',default=DATASET)
    args=parser.parse_args()
    if any(t==DATABASE or not re.fullmatch(r'olist_(?:llm_full|accept)_[0-9a-f]{8}',t) for t in args.targets):
        parser.error('Only isolated LLM/full acceptance targets may be verified')
    with connect() as conn:
        before=version_token(conn,DATABASE)
    reference=source_seller_reference(args.source)
    expected=mart_digests(DATABASE,expected_city_overrides=reference['city_overrides'])
    out={'at':datetime.now(timezone.utc).isoformat(),'external_llm_calls':0,'source_database':DATABASE,
         'source_csv':reference['path'],'source_csv_sha256':reference['sha256'],
         'baseline_source_differences':reference['baseline_source_differences'],
         'expected':expected,'targets':[],'status':'RUNNING'}
    for target in args.targets:
        actual=mart_digests(target)
        try:
            raw_source_check=verify_seller_source(target,reference)
        except AssertionError as error:
            raw_source_check={'all_columns_match_source':False,'error':str(error)}
        ok=actual==expected and raw_source_check['all_columns_match_source']
        out['targets'].append({'database':target,'status':'PASS' if ok else 'FAIL','actual':actual,
                               'seller_source_check':raw_source_check})
        print(('PASS' if ok else 'FAIL')+' FULL_MART_CONTENT '+target,flush=True)
    with connect() as conn:
        out['formal_checksum_unchanged']=version_token(conn,DATABASE)==before
    out['status']='PASS' if all(t['status']=='PASS' for t in out['targets']) and out['formal_checksum_unchanged'] else 'FAIL'
    path=ARTIFACTS/('model_mart_comparison_'+uuid.uuid4().hex[:8]+'.json')
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT='+str(path),flush=True)
    return 0 if out['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
