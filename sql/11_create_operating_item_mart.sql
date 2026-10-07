-- 阶段四（4.1 数据准备）：经营商品项明细 mart
-- 粒度：一行一个订单商品项（order_id + order_item_id）。
-- 用途：准确分析销量、品类、卖家和运费贡献，避免使用订单主品类造成错误归类。

USE olist_ecommerce;
SET NAMES utf8mb4;

DROP TABLE IF EXISTS mart_order_item_business;

CREATE TABLE mart_order_item_business (
    order_id                         CHAR(32)       NOT NULL,
    order_item_id                    INT            NOT NULL,
    order_status                     VARCHAR(20)    NOT NULL,
    order_purchase_timestamp         DATETIME       NOT NULL,
    purchase_month                   DATE           NOT NULL COMMENT '购买月份首日，便于 BI 建立日期关系',

    customer_id                      CHAR(32)       NOT NULL,
    customer_unique_id               CHAR(32)       NOT NULL,
    customer_city                    VARCHAR(120)   NOT NULL,
    customer_state                   CHAR(2)        NOT NULL,

    product_id                       CHAR(32)       NOT NULL,
    category_name                    VARCHAR(100)   NOT NULL,
    product_weight_g                 INT            NULL,
    product_volume_cm3               DECIMAL(20,2)  NULL,

    seller_id                        CHAR(32)       NOT NULL,
    seller_city                      VARCHAR(120)   NULL,
    seller_state                     CHAR(2)        NULL,
    is_cross_state                   TINYINT        NULL,

    shipping_limit_date              DATETIME       NOT NULL,
    item_price                       DECIMAL(12,2)  NOT NULL,
    item_freight_value               DECIMAL(12,2)  NOT NULL,
    item_value_including_freight     DECIMAL(14,2)  NOT NULL,
    item_freight_ratio               DECIMAL(10,6)  NOT NULL,

    is_delivered                     TINYINT        NOT NULL,
    is_canceled_or_unavailable       TINYINT        NOT NULL,
    has_complete_product_dimension   TINYINT        NOT NULL,
    has_complete_seller_dimension    TINYINT        NOT NULL,

    PRIMARY KEY (order_id, order_item_id),
    KEY idx_item_business_month (purchase_month),
    KEY idx_item_business_status (order_status),
    KEY idx_item_business_customer_state (customer_state),
    KEY idx_item_business_category (category_name),
    KEY idx_item_business_seller (seller_id),
    KEY idx_item_business_seller_state (seller_state)
) ENGINE = InnoDB;

INSERT INTO mart_order_item_business (
    order_id,
    order_item_id,
    order_status,
    order_purchase_timestamp,
    purchase_month,
    customer_id,
    customer_unique_id,
    customer_city,
    customer_state,
    product_id,
    category_name,
    product_weight_g,
    product_volume_cm3,
    seller_id,
    seller_city,
    seller_state,
    is_cross_state,
    shipping_limit_date,
    item_price,
    item_freight_value,
    item_value_including_freight,
    item_freight_ratio,
    is_delivered,
    is_canceled_or_unavailable,
    has_complete_product_dimension,
    has_complete_seller_dimension
)
SELECT i.order_id,
       i.order_item_id,
       o.order_status,
       o.order_purchase_timestamp,
       CAST(DATE_FORMAT(o.order_purchase_timestamp, '%Y-%m-01') AS DATE),
       c.customer_id,
       c.customer_unique_id,
       c.customer_city,
       c.customer_state,
       i.product_id,
       COALESCE(t.product_category_name_english,
                p.product_category_name,
                'unknown') AS category_name,
       p.product_weight_g,
       CASE
           WHEN p.product_length_cm IS NOT NULL
            AND p.product_height_cm IS NOT NULL
            AND p.product_width_cm IS NOT NULL
           THEN CAST(CAST(p.product_length_cm AS DECIMAL(20,2))
                     * p.product_height_cm
                     * p.product_width_cm AS DECIMAL(20,2))
           ELSE NULL
       END AS product_volume_cm3,
       i.seller_id,
       s.seller_city,
       s.seller_state,
       CASE
           WHEN s.seller_state IS NULL THEN NULL
           WHEN s.seller_state = c.customer_state THEN 0
           ELSE 1
       END AS is_cross_state,
       i.shipping_limit_date,
       i.price,
       i.freight_value,
       CAST(i.price + i.freight_value AS DECIMAL(14,2)),
       CAST(i.freight_value / NULLIF(i.price + i.freight_value, 0)
            AS DECIMAL(10,6)),
       CASE WHEN o.order_status = 'delivered' THEN 1 ELSE 0 END,
       CASE WHEN o.order_status IN ('canceled', 'unavailable') THEN 1 ELSE 0 END,
       CASE WHEN p.product_id IS NOT NULL THEN 1 ELSE 0 END,
       CASE WHEN s.seller_id IS NOT NULL THEN 1 ELSE 0 END
FROM raw_order_items i
JOIN raw_orders o
  ON i.order_id = o.order_id
JOIN raw_customers c
  ON o.customer_id = c.customer_id
LEFT JOIN raw_products p
  ON i.product_id = p.product_id
LEFT JOIN raw_category_translation t
  ON p.product_category_name = t.product_category_name
LEFT JOIN raw_sellers s
  ON i.seller_id = s.seller_id;

ANALYZE TABLE mart_order_item_business;

SELECT COUNT(*) AS mart_order_item_business_rows
FROM mart_order_item_business;
