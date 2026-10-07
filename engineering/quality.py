from __future__ import annotations

from decimal import Decimal

from engineering.contracts import TABLES
from engineering.config import ident
from engineering.pipeline import scalar


def check(cur, name, sql, expected=0, *, warning=False):
    actual = scalar(cur, sql)
    okay = actual == expected
    return {'name': name, 'status': 'PASS' if okay else ('WARNING' if warning else 'FAIL'),
            'actual': str(actual) if isinstance(actual, Decimal) else actual, 'expected': str(expected) if isinstance(expected, Decimal) else expected}


def raw_checks(connection):
    with connection.cursor() as cur:
        checks = []
        for name, sql in (
            ('已送达订单商品项完整性', "SELECT COUNT(*) FROM raw_orders o WHERE o.order_status='delivered' AND NOT EXISTS (SELECT 1 FROM raw_order_items i WHERE i.order_id=o.order_id)"),
            ('订单—客户关联完整性', 'SELECT COUNT(*) FROM raw_orders o LEFT JOIN raw_customers c ON o.customer_id=c.customer_id WHERE c.customer_id IS NULL'),
            ('商品项—订单关联完整性', 'SELECT COUNT(*) FROM raw_order_items i LEFT JOIN raw_orders o ON i.order_id=o.order_id WHERE o.order_id IS NULL'),
            ('商品项—商品关联完整性', 'SELECT COUNT(*) FROM raw_order_items i LEFT JOIN raw_products p ON i.product_id=p.product_id WHERE p.product_id IS NULL'),
            ('商品项—卖家关联完整性', 'SELECT COUNT(*) FROM raw_order_items i LEFT JOIN raw_sellers s ON i.seller_id=s.seller_id WHERE s.seller_id IS NULL'),
            ('支付—订单关联完整性', 'SELECT COUNT(*) FROM raw_order_payments i LEFT JOIN raw_orders o ON i.order_id=o.order_id WHERE o.order_id IS NULL'),
            ('评价—订单关联完整性', 'SELECT COUNT(*) FROM raw_order_reviews i LEFT JOIN raw_orders o ON i.order_id=o.order_id WHERE o.order_id IS NULL'),
            ('评分取值范围', 'SELECT COUNT(*) FROM raw_order_reviews WHERE review_score NOT BETWEEN 1 AND 5'),
            ('商品金额与运费非负', 'SELECT COUNT(*) FROM raw_order_items WHERE price<0 OR freight_value<0'),
            ('支付金额非负', 'SELECT COUNT(*) FROM raw_order_payments WHERE payment_value<0'),
        ):
            checks.append(check(cur, name, sql))
        for name, sql in (
            ('缺少支付记录的订单', 'SELECT COUNT(*) FROM raw_orders o LEFT JOIN raw_order_payments p ON o.order_id=p.order_id WHERE p.order_id IS NULL'),
            ('非标准履约时间顺序', 'SELECT COUNT(*) FROM raw_orders WHERE order_approved_at<order_purchase_timestamp OR order_delivered_carrier_date<order_approved_at OR order_delivered_customer_date<order_delivered_carrier_date'),
            ('缺少品类翻译', 'SELECT COUNT(DISTINCT p.product_category_name) FROM raw_products p LEFT JOIN raw_category_translation t ON p.product_category_name=t.product_category_name WHERE p.product_category_name IS NOT NULL AND t.product_category_name IS NULL'),
        ):
            checks.append(check(cur, name, sql, warning=True))
        return checks


def model_checks(connection):
    with connection.cursor() as cur:
        checks = []
        for name, sql in (
            ('Staging 邮编粒度', 'SELECT (SELECT COUNT(*) FROM stg_geolocation_zip)-(SELECT COUNT(DISTINCT geolocation_zip_code_prefix) FROM raw_geolocation)'),
            ('Staging 支付粒度', 'SELECT (SELECT COUNT(*) FROM stg_order_payments)-(SELECT COUNT(DISTINCT order_id) FROM raw_order_payments)'),
            ('Staging 评价粒度', 'SELECT (SELECT COUNT(*) FROM stg_order_reviews)-(SELECT COUNT(DISTINCT order_id) FROM raw_order_reviews)'),
            ('Staging 商品汇总粒度', 'SELECT (SELECT COUNT(*) FROM stg_order_items_summary)-(SELECT COUNT(DISTINCT order_id) FROM raw_order_items)'),
            ('Mart 订单保全', 'SELECT (SELECT COUNT(*) FROM mart_order_delivery)-(SELECT COUNT(*) FROM raw_orders)'),
            ('Mart 商品项保全', 'SELECT (SELECT COUNT(*) FROM mart_order_item_business)-(SELECT COUNT(*) FROM raw_order_items)'),
            ('Mart 订单—卖家粒度', 'SELECT (SELECT COUNT(*) FROM mart_order_seller_delivery)-(SELECT COUNT(*) FROM (SELECT order_id,seller_id FROM raw_order_items GROUP BY order_id,seller_id) x)'),
            ('最终评价选择', 'SELECT COUNT(*) FROM stg_order_reviews s JOIN raw_order_reviews r ON s.order_id=r.order_id WHERE r.review_answer_timestamp>s.review_answer_timestamp'),
            ('低评分标记', 'SELECT COUNT(*) FROM mart_order_delivery WHERE review_score IS NOT NULL AND is_low_score<>(review_score<=3)'),
            ('延迟标记', "SELECT COUNT(*) FROM mart_order_delivery WHERE is_delivery_analysis_eligible=1 AND is_late_delivery<>(DATE(order_delivered_customer_date)>DATE(order_estimated_delivery_date))"),
            ('商品来源标记', 'SELECT COUNT(*) FROM mart_order_delivery o LEFT JOIN stg_order_items_summary i ON o.order_id=i.order_id WHERE o.has_item_record<>(i.order_id IS NOT NULL)'),
            ('支付来源标记', 'SELECT COUNT(*) FROM mart_order_delivery o LEFT JOIN stg_order_payments i ON o.order_id=i.order_id WHERE o.has_payment_record<>(i.order_id IS NOT NULL)'),
        ):
            checks.append(check(cur, name, sql))
        for name, left, right in (
            ('支付 Raw→Staging', 'SELECT COALESCE(SUM(payment_value),0) FROM raw_order_payments', 'SELECT COALESCE(SUM(payment_total),0) FROM stg_order_payments'),
            ('支付 Staging→Mart', 'SELECT COALESCE(SUM(payment_total),0) FROM stg_order_payments', 'SELECT COALESCE(SUM(payment_total),0) FROM mart_order_delivery'),
            ('商品 Raw→Staging', 'SELECT COALESCE(SUM(price),0) FROM raw_order_items', 'SELECT COALESCE(SUM(product_value),0) FROM stg_order_items_summary'),
            ('商品 Staging→订单 Mart', 'SELECT COALESCE(SUM(product_value),0) FROM stg_order_items_summary', 'SELECT COALESCE(SUM(product_value),0) FROM mart_order_delivery'),
            ('商品 Raw→卖家 Mart', 'SELECT COALESCE(SUM(price),0) FROM raw_order_items', 'SELECT COALESCE(SUM(seller_product_value),0) FROM mart_order_seller_delivery'),
            ('商品 Raw→商品项 Mart', 'SELECT COALESCE(SUM(price),0) FROM raw_order_items', 'SELECT COALESCE(SUM(item_price),0) FROM mart_order_item_business'),
            ('运费 Raw→Staging', 'SELECT COALESCE(SUM(freight_value),0) FROM raw_order_items', 'SELECT COALESCE(SUM(freight_value),0) FROM stg_order_items_summary'),
            ('运费 Raw→订单 Mart', 'SELECT COALESCE(SUM(freight_value),0) FROM raw_order_items', 'SELECT COALESCE(SUM(freight_value),0) FROM mart_order_delivery'),
            ('运费 Raw→卖家 Mart', 'SELECT COALESCE(SUM(freight_value),0) FROM raw_order_items', 'SELECT COALESCE(SUM(seller_freight_value),0) FROM mart_order_seller_delivery'),
            ('运费 Raw→商品项 Mart', 'SELECT COALESCE(SUM(freight_value),0) FROM raw_order_items', 'SELECT COALESCE(SUM(item_freight_value),0) FROM mart_order_item_business'),
        ):
            a, b = scalar(cur, left), scalar(cur, right)
            checks.append({'name': name, 'status': 'PASS' if a == b else 'FAIL', 'actual': str(a-b), 'expected': '0', 'raw': str(a), 'modeled': str(b)})
        checks.append(check(cur, '订单地理信息缺失', 'SELECT COUNT(*) FROM mart_order_delivery WHERE customer_lat IS NULL', warning=True))
        return checks
