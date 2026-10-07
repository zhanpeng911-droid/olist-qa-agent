from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from engineering.contracts import CONTRACTS, STAGING, MARTS, TABLES


ANONYMOUS = frozenset('DATE_FORMAT TIMESTAMPDIFF TIMESTAMPADD DAYNAME WEEKDAY FIELD '
                      'FORMAT IFNULL NULLIF COALESCE CONCAT CONCAT_WS ROUND TRUNCATE '
                      'RADIANS DEGREES ACOS ASIN ATAN2 SQRT POW POWER LOG LN EXP '
                      'JSON_ARRAY JSON_EXTRACT JSON_UNQUOTE STR_TO_DATE'.split())
DENIED_FUNCTIONS = frozenset('SLEEP BENCHMARK LOAD_FILE GET_LOCK RELEASE_LOCK IS_FREE_LOCK '
                             'IS_USED_LOCK RELEASE_ALL_LOCKS MASTER_POS_WAIT SOURCE_POS_WAIT '
                             'WAIT_FOR_EXECUTED_GTID_SET SYS_EXEC SYS_EVAL USER CURRENT_USER '
                             'SESSION_USER SYSTEM_USER CURRENT_ROLE DATABASE VERSION'.split())


def parse(sql: str):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 100_000:
        raise ValueError('SQL 不能为空，且不能超过 100,000 字符')
    # MySQL executable comments and hints are never trusted model input.
    try:
        if '\x00' in sql or any(t.comments for t in sqlglot.tokenize(sql, read='mysql')):
            raise ValueError('不允许SQL注释或空字符；请提交纯SQL')
        trees = sqlglot.parse(sql, read='mysql', error_level='RAISE')
    except (ParseError, TokenError):
        raise ValueError('SQL 无法解析，请修正 MySQL 8 语法') from None
    if len(trees) != 1 or trees[0] is None:
        raise ValueError('每次只允许一条 SQL 语句')
    tree = trees[0]
    for node in tree.walk():
        if isinstance(node, (exp.Command, exp.Into, exp.Lock, exp.Parameter, exp.Var)):
            # Var also represents legitimate type/interval tokens: permit these below.
            if isinstance(node, exp.Var) and re.fullmatch(r'(DAY|HOUR|MINUTE|SECOND|MONTH|YEAR|WEEK|utf8mb4|InnoDB|utf8mb4_0900_ai_ci)', node.name, re.I):
                continue
            raise ValueError('SQL 包含不允许的命令、文件输出、锁或变量')
        if isinstance(node, exp.With) and node.args.get('recursive'):
            raise ValueError('暂不支持递归 CTE')
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if name in DENIED_FUNCTIONS or (isinstance(node, exp.Anonymous) and name not in ANONYMOUS):
                raise ValueError('不允许该函数；仅支持经批准的 SQL 内置函数')
    if len(list(tree.find_all(exp.Join))) > 8:
        raise ValueError('单条查询最多支持八个 JOIN，请先分层聚合')
    return tree


def tables_allowed(tree, database, allowed):
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier) or table.catalog:
            raise ValueError('不允许动态表名或跨服务器访问')
        if table.db and table.db != database:
            raise ValueError('不允许跨数据库访问')
        if table.name in ctes and not table.db:
            continue
        if table.name not in allowed:
            raise ValueError('SQL 引用了未授权的数据表')


def select(sql, database, allowed=TABLES, max_rows=5000, *, full_export=False):
    tree = parse(sql)
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError('取数仅允许 SELECT / 非递归 WITH 查询')
    if tree.find(exp.Create) or tree.find(exp.Insert) or tree.find(exp.Delete) or tree.find(exp.Update):
        raise ValueError('查询中不能包含写入语句')
    tables_allowed(tree, database, allowed)
    if not list(tree.find_all(exp.Table)):
        raise ValueError('查询必须引用授权业务表')
    limit = tree.args.get('limit')
    if limit:
        count = limit.expression
        if not isinstance(count, exp.Literal) or count.is_string or not count.this.isdigit():
            raise ValueError('LIMIT 需要非负整数字面量')
    if full_export:
        # Only a local, explicit export request reaches this flag. Preserve the
        # user's own LIMIT/OFFSET; remove no predicate or semantic restriction.
        return tree.sql(dialect='mysql'), tree.sql(dialect='mysql'), None
    maximum = max(1, min(int(max_rows), 100_000))
    if limit:
        if int(count.this) <= maximum:
            return tree.sql(dialect='mysql'), tree.sql(dialect='mysql'), maximum
    bounded = tree.copy().limit(maximum + 1)
    return tree.sql(dialect='mysql'), bounded.sql(dialect='mysql'), maximum


def candidate(sql, database):
    tree = parse(sql)
    if not isinstance(tree, (exp.Create, exp.Insert, exp.Drop)):
        raise ValueError('候选库仅允许 CREATE TABLE、INSERT INTO … SELECT、DROP TABLE')
    if isinstance(tree, (exp.Create, exp.Drop)) and tree.args.get('kind') != 'TABLE':
        raise ValueError('仅允许创建或重建普通表，不允许视图、过程、触发器')
    target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
    if not isinstance(target, exp.Table) or target.name not in (*STAGING, *MARTS):
        raise ValueError('只能修改四张 Staging 和三张 Mart；Raw 不可由模型改写')
    tables_allowed(tree, database, TABLES)
    if isinstance(tree, exp.Insert) and not isinstance(tree.expression, (exp.Select, exp.Union)):
        raise ValueError('建模只允许 INSERT … SELECT，不能编造 VALUES 业务数据')
    if isinstance(tree, exp.Create):
        if tree.find(exp.ForeignKey):
            raise ValueError('候选建模不支持外键约束；关系完整性由固定质量门验证')
        properties = tree.args.get('properties')
        for property_ in properties.expressions if properties else []:
            name = type(property_).__name__
            if name not in ('EngineProperty', 'CharacterSetProperty', 'CollateProperty'):
                raise ValueError('不支持该建表属性；禁止外部引擎、目录和表空间配置')
            if name == 'EngineProperty' and property_.this.name.lower() != 'innodb':
                raise ValueError('建模仅允许InnoDB引擎')
        if tree.args.get('clone') or tree.args.get('replace'):
            raise ValueError('不支持克隆或替换表')
        for property_ in tree.find_all(exp.Property):
            if 'directory' in property_.sql(dialect='mysql').lower():
                raise ValueError('不支持指定表文件目录')
    return tree.sql(dialect='mysql'), target.name
