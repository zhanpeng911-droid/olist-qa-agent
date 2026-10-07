from __future__ import annotations

from datetime import date, datetime

from engineering.config import connect, ident
from engineering.contracts import TABLES
from engineering.pipeline import existing_tables


def validate_filters(start, end, state):
    for value in (start, end):
        if value:
            datetime.strptime(value, '%Y-%m')
    if start and end and start > end:
        raise ValueError('开始月份不能晚于结束月份')
    from engineering.contracts import STATES
    if state and state not in STATES:
        raise ValueError('未知客户州')


def where(start, end, state, alias=''):
    validate_filters(start, end, state)
    prefix = alias + '.' if alias else ''
    terms, params = ['1=1'], []
    if start:
        terms.append(f'{prefix}order_purchase_timestamp >= %s')
        params.append(start + '-01')
    if end:
        terms.append(f'{prefix}order_purchase_timestamp < DATE_ADD(%s,INTERVAL 1 MONTH)')
        params.append(end + '-01')
    if state:
        terms.append(f'{prefix}customer_state=%s')
        params.append(state)
    return ' AND '.join(terms), params


AGGREGATES = """
 COUNT(*) all_orders,
 SUM(order_status IN ('canceled','unavailable')) canceled_orders,
 COUNT(CASE WHEN order_status='delivered' THEN 1 END) delivered_orders,
 COUNT(DISTINCT CASE WHEN order_status='delivered' THEN customer_unique_id END) customers,
 SUM(CASE WHEN order_status='delivered' THEN product_value END) product_value,
 SUM(CASE WHEN order_status='delivered' THEN freight_value END) freight_value,
 SUM(CASE WHEN order_status='delivered' THEN item_count END) items,
 COUNT(CASE WHEN order_status='delivered' AND is_delivery_analysis_eligible=1 THEN 1 END) delivery_sample,
 COUNT(CASE WHEN order_status='delivered' AND is_delivery_analysis_eligible=1 AND is_late_delivery=1 THEN 1 END) late_orders,
 COUNT(CASE WHEN order_status='delivered' AND review_score BETWEEN 1 AND 5 THEN 1 END) review_sample,
 COUNT(CASE WHEN order_status='delivered' AND review_score BETWEEN 1 AND 3 THEN 1 END) low_score_orders
"""


def ratio(a, b):
    return float(a)/float(b) if a is not None and b else None


def enrich(row):
    row.update(aov=ratio(row['product_value'], row['delivered_orders']),
               orders_per_customer=ratio(row['delivered_orders'], row['customers']),
               items_per_order=ratio(row['items'], row['delivered_orders']),
               price_per_item=ratio(row['product_value'], row['items']),
               late_rate=ratio(row['late_orders'], row['delivery_sample']),
               low_score_rate=ratio(row['low_score_orders'], row['review_sample']),
               canceled_rate=ratio(row['canceled_orders'], row['all_orders']))
    return row


def overview(database, start=None, end=None, state=None):
    condition, params = where(start, end, state)
    with connect(database) as conn:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(f'SELECT {AGGREGATES} FROM mart_order_delivery WHERE {condition}', params)
            summary = enrich(cur.fetchone())
            cur.execute(f"SELECT DATE_FORMAT(order_purchase_timestamp,'%%Y-%%m') month,{AGGREGATES} FROM mart_order_delivery WHERE {condition} GROUP BY month ORDER BY month", params)
            monthly = [enrich(r) for r in cur.fetchall()]
    # No-delivered months are absent observations for the delivered-order KPIs.
    for row in monthly:
        if not row['delivered_orders']:
            for key in ('delivered_orders', 'customers', 'product_value', 'freight_value', 'items'):
                row[key] = None
    return {'summary': summary, 'monthly': monthly, 'filters': {'start': start, 'end': end, 'state': state}}


def growth(database, start=None, end=None, state=None, year_a=2017, year_b=2018, month_from=1, month_to=8):
    validate_filters(start, end, state)
    if not 1900 <= year_a < year_b <= 9998 or not 1 <= month_from <= month_to <= 12:
        raise ValueError('同期窗口需要有效年份和相同月份范围')
    with connect(database) as conn:
        conn.begin()
        with conn.cursor() as cur:
            comparison = []
            for year in (year_a, year_b):
                condition, params = where(f'{year}-{month_from:02d}', f'{year}-{month_to:02d}', state)
                cur.execute(f'SELECT {AGGREGATES} FROM mart_order_delivery WHERE {condition}', params)
                comparison.append(dict(year=year, **enrich(cur.fetchone())))
            condition, params = where(start, end, state)
            cur.execute(f"SELECT DATE_FORMAT(order_purchase_timestamp,'%%Y-%%m') month,{AGGREGATES} FROM mart_order_delivery WHERE {condition} GROUP BY month ORDER BY month", params)
            monthly = [enrich(r) for r in cur.fetchall()]
            ranking = {}
            concentrations = {}
            for dimension, table, value in (
                ('state', 'mart_order_delivery', 'product_value'),
                ('category', 'mart_order_item_business', 'item_price'),
                ('seller', 'mart_order_item_business', 'item_price'),
            ):
                column = {'state': 'customer_state', 'category': 'category_name', 'seller': 'seller_id'}[dimension]
                cur.execute(f'SELECT {ident(column)} name,SUM({ident(value)}) product_value,COUNT(DISTINCT order_id) orders FROM {ident(table)} WHERE {condition} AND order_status=\'delivered\' GROUP BY {ident(column)} ORDER BY product_value DESC,name', params)
                rows = cur.fetchall()
                total = sum((r['product_value'] or 0) for r in rows)
                for row in rows:
                    row['amount_share'] = ratio(row['product_value'], total)
                    # Category/seller orders may overlap and must not be summed as distinct orders.
                concentrations[dimension] = {'groups': len(rows), 'total': total,
                    'top1_share': rows[0]['amount_share'] if rows else None,
                    'top10_share': sum(r['amount_share'] or 0 for r in rows[:10]) if rows else None,
                    'hhi': sum((r['amount_share'] or 0)**2 for r in rows)*10000 if rows else None}
                ranking[dimension] = rows[:10]
    rates = {}
    for key in ('product_value','delivered_orders','customers','aov','orders_per_customer','items','items_per_order','price_per_item'):
        a, b = comparison[0][key], comparison[1][key]
        rates[key] = float(b)/float(a)-1 if a and b is not None else None
    return {'comparison': comparison, 'change': rates, 'monthly': monthly, 'ranking': ranking,
            'concentrations': concentrations, 'window': {'year_a': year_a,'year_b': year_b,'month_from': month_from,'month_to': month_to}}


def metadata(database):
    result = {'database': database, 'connected': False, 'ready': False, 'tables': [], 'defaults': {}, 'states': [], 'months': []}
    try:
        with connect() as control:
            present = existing_tables(control, database)
        result['connected'] = True
        if not present:
            return result
        with connect(database) as conn, conn.cursor() as cur:
            conn.begin()
            for table in TABLES:
                if table in present:
                    cur.execute(f'SELECT COUNT(*) n FROM {ident(table)}')
                    result['tables'].append({'table':table,'rows':cur.fetchone()['n'],'layer':table.split('_')[0]})
            result['ready'] = all(t in present for t in ('mart_order_delivery','mart_order_item_business','mart_order_seller_delivery'))
            if result['ready']:
                cur.execute("SELECT DISTINCT DATE_FORMAT(order_purchase_timestamp,'%Y-%m') month FROM mart_order_delivery WHERE order_status='delivered' ORDER BY month")
                result['months'] = [r['month'] for r in cur.fetchall()]
                cur.execute('SELECT DISTINCT customer_state FROM mart_order_delivery ORDER BY customer_state')
                result['states'] = [r['customer_state'] for r in cur.fetchall()]
                cur.execute('SELECT MIN(order_purchase_timestamp) start,MAX(order_purchase_timestamp) end FROM mart_order_delivery')
                result['source_period'] = cur.fetchone()
                if result['months']:
                    latest = result['months'][-1]
                    latest_year = int(latest[:4])
                    result['defaults'] = {'start': '2017-01' if '2017-01' in result['months'] else result['months'][0],
                        'end':latest,'year_a':latest_year-1,'year_b':latest_year,'month_from':1,'month_to':int(latest[5:])}
            if '_eng_version' in present:
                cur.execute('SELECT * FROM _eng_version')
                result['version'] = cur.fetchone()
    except Exception as error:
        result['error'] = str(error.args[0]) if error.args else '数据库连接失败'
    return result
