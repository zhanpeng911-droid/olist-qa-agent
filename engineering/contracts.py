from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Contract:
    filename: str
    columns: tuple[str, ...]
    keys: tuple[str, ...]
    nullable: frozenset[str] = frozenset()


def spec(filename, columns, keys, nullable=''):
    return Contract(filename, tuple(columns.split()), tuple(keys.split()), frozenset(nullable.split()))


CONTRACTS = {
    'raw_customers': spec('olist_customers_dataset.csv', 'customer_id customer_unique_id customer_zip_code_prefix customer_city customer_state', 'customer_id'),
    'raw_geolocation': spec('olist_geolocation_dataset.csv', 'geolocation_zip_code_prefix geolocation_lat geolocation_lng geolocation_city geolocation_state', ''),
    'raw_order_items': spec('olist_order_items_dataset.csv', 'order_id order_item_id product_id seller_id shipping_limit_date price freight_value', 'order_id order_item_id'),
    'raw_order_payments': spec('olist_order_payments_dataset.csv', 'order_id payment_sequential payment_type payment_installments payment_value', 'order_id payment_sequential'),
    'raw_order_reviews': spec('olist_order_reviews_dataset.csv', 'review_id order_id review_score review_comment_title review_comment_message review_creation_date review_answer_timestamp', '', 'review_comment_title review_comment_message'),
    'raw_orders': spec('olist_orders_dataset.csv', 'order_id customer_id order_status order_purchase_timestamp order_approved_at order_delivered_carrier_date order_delivered_customer_date order_estimated_delivery_date', 'order_id', 'order_approved_at order_delivered_carrier_date order_delivered_customer_date'),
    'raw_products': spec('olist_products_dataset.csv', 'product_id product_category_name product_name_lenght product_description_lenght product_photos_qty product_weight_g product_length_cm product_height_cm product_width_cm', 'product_id', 'product_category_name product_name_lenght product_description_lenght product_photos_qty product_weight_g product_length_cm product_height_cm product_width_cm'),
    'raw_sellers': spec('olist_sellers_dataset.csv', 'seller_id seller_zip_code_prefix seller_city seller_state', 'seller_id'),
    'raw_category_translation': spec('product_category_name_translation.csv', 'product_category_name product_category_name_english', 'product_category_name'),
}
STAGING = ('stg_geolocation_zip', 'stg_order_payments', 'stg_order_reviews', 'stg_order_items_summary')
MARTS = ('mart_order_delivery', 'mart_order_seller_delivery', 'mart_order_item_business')
TABLES = (*CONTRACTS, *STAGING, *MARTS)
ORDER_STATUSES = frozenset('created approved invoiced processing shipped delivered canceled unavailable'.split())
PAYMENTS = frozenset('credit_card boleto voucher debit_card not_defined'.split())
STATES = frozenset('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split())
INTS = frozenset('order_item_id payment_sequential payment_installments review_score product_name_lenght product_description_lenght product_photos_qty product_weight_g product_length_cm product_height_cm product_width_cm'.split())
MONEY = frozenset('price freight_value payment_value'.split())
