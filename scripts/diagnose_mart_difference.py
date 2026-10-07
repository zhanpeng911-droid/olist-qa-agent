"""Read-only, bounded diagnostics for an isolated model-built Mart."""
import argparse
import json
import re
import uuid
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from agent_core.database import catalog, clean
from engineering.config import ARTIFACTS, DATABASE, connect, ident
from engineering.contracts import TABLES


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target',required=True)
    target=parser.parse_args().target
    if target==DATABASE or not re.fullmatch(r'olist_(?:llm_full|accept)_[0-9a-f]{8}',target):
        parser.error('Only isolated published test targets are allowed')
    schema=catalog(DATABASE)['tables'];out={'target':target,'tables':[]}
    with connect() as conn,conn.cursor() as cur:
        for table in TABLES[13:]:
            fields=[f['name'] for f in schema[table]];keys=[f['name'] for f in schema[table] if f['key']=='PRI']
            join=' AND '.join(f'a.{ident(k)}=b.{ident(k)}' for k in keys)
            tables=f'{ident(DATABASE)}.{ident(table)} a JOIN {ident(target)}.{ident(table)} b ON {join}'
            select=','.join(f'SUM(NOT(a.{ident(f)} <=> b.{ident(f)})) AS {ident(f)}' for f in fields)
            cur.execute(f'SELECT {select} FROM {tables}')
            counts={f:int(n) for f,n in cur.fetchone().items() if n}
            item={'table':table,'different_fields':counts}
            if counts:
                changed=list(counts)[:6]
                cols=[f'a.{ident(k)}' for k in keys]
                for f in changed:
                    cols += [f'a.{ident(f)} AS {ident("old_"+f)}', f'b.{ident(f)} AS {ident("new_"+f)}']
                predicate=' OR '.join(f'NOT(a.{ident(f)} <=> b.{ident(f)})' for f in changed)
                cur.execute(f'SELECT {",".join(cols)} FROM {tables} WHERE {predicate} LIMIT 3')
                item['examples']=clean(cur.fetchall())
            out['tables'].append(item)
            print(json.dumps(item,ensure_ascii=False),flush=True)
    path=ARTIFACTS/('model_mart_differences_'+uuid.uuid4().hex[:8]+'.json')
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT='+str(path))


if __name__=='__main__':
    main()
