-- Olist 项目：构建 mart 业务分析层
-- 执行顺序：staging 层质量检查全部通过后执行。
-- 本脚本只删除并重建 mart_ 表，不修改 raw_ 或 stg_ 表。

USE olist_ecommerce;
SET NAMES utf8mb4;

-- ============================================================
-- 1. 订单履约分析宽表：一行一笔订单
-- 保留全部订单；缺失支付、商品、评价或履约时间时使用标记和 NULL 表达。
-- 延迟按自然日判断：实际送达日期晚于预计送达日期。
-- ============================================================
DROP TABLE IF EXISTS mart_order_seller_delivery;
DROP TABLE IF EXISTS mart_order_delivery;

CREATE TABLE mart_order_delivery (
    order_id                         CHAR(32)       NOT NULL,
    customer_id                      CHAR(32)       NOT NULL,
    customer_unique_id               CHAR(32)       NOT NULL,
    order_status                     VARCHAR(20)    NOT NULL,
    order_purchase_timestamp         DATETIME       NOT NULL,
    order_approved_at                DATETIME       NULL,
    order_delivered_carrier_date     DATETIME       NULL,
    order_delivered_customer_date    DATETIME       NULL,
    order_estimated_delivery_date    DATETIME       NOT NULL,

    customer_zip_code_prefix         CHAR(5)        NOT NULL,
    customer_city                    VARCHAR(120)   NOT NULL,
    customer_state                   CHAR(2)        NOT NULL,
    customer_lat                     DECIMAL(23,20) NULL,
    customer_lng                     DECIMAL(23,20) NULL,

    has_payment_record               TINYINT        NOT NULL,
    payment_total                    DECIMAL(14,2)  NULL,
    payment_record_count             INT            NULL,
    payment_method_count             INT            NULL,
    primary_payment_type             VARCHAR(30)    NULL,
    max_payment_installments         INT            NULL,
    is_multi_payment_record          TINYINT        NULL,
    is_multi_payment_method          TINYINT        NULL,

    has_item_record                  TINYINT        NOT NULL,
    item_count                       INT            NULL,
    distinct_product_count           INT            NULL,
    distinct_seller_count            INT            NULL,
    product_value                    DECIMAL(14,2)  NULL,
    freight_value                    DECIMAL(14,2)  NULL,
    order_value_including_freight    DECIMAL(14,2)  NULL,
    freight_ratio                    DECIMAL(10,6)  NULL,
    average_item_price               DECIMAL(14,2)  NULL,
    total_weight_g                   DECIMAL(18,2)  NULL,
    total_volume_cm3                 DECIMAL(20,2)  NULL,
    missing_product_attribute_items  INT            NULL,
    earliest_shipping_limit_date     DATETIME       NULL,
    latest_shipping_limit_date       DATETIME       NULL,
    primary_category_name            VARCHAR(100)   NULL,

    has_review_record                TINYINT        NOT NULL,
    selected_review_id               CHAR(32)       NULL,
    review_score                     TINYINT        NULL,
    is_low_score                     TINYINT        NULL COMMENT '主口径：review_score <= 3',
    is_strict_negative_score         TINYINT        NULL COMMENT '对照口径：review_score <= 2',
    has_review_text                  TINYINT        NULL,
    review_response_hours            DECIMAL(12,2)  NULL,

    payment_approval_hours           DECIMAL(14,2)  NULL,
    handover_hours                   DECIMAL(14,2)  NULL COMMENT '支付批准到交给承运方',
    shipping_hours                   DECIMAL(14,2)  NULL COMMENT '交给承运方到客户签收',
    total_fulfillment_hours          DECIMAL(14,2)  NULL COMMENT '下单到客户签收',
    is_delivery_analysis_eligible    TINYINT        NOT NULL COMMENT '已送达且实际/预计送达时间完整',
    has_complete_fulfillment_time    TINYINT        NOT NULL,
    delivery_variance_days           INT            NULL COMMENT '实际送达日期减预计送达日期；正数为延迟',
    is_late_delivery                 TINYINT        NULL,
    late_days                        INT            NULL,
    early_days                       INT            NULL,

    PRIMARY KEY (order_id),
    KEY idx_mart_order_purchase (order_purchase_timestamp),
    KEY idx_mart_order_status (order_status),
    KEY idx_mart_customer_state (customer_state),
    KEY idx_mart_category (primary_category_name),
    KEY idx_mart_late (is_delivery_analysis_eligible, is_late_delivery),
    KEY idx_mart_low_score (has_review_record, is_low_score)
) ENGINE = InnoDB;

INSERT INTO mart_order_delivery (
    order_id,
    customer_id,
    customer_unique_id,
    order_status,
    order_purchase_timestamp,
    order_approved_at,
    order_delivered_carrier_date,
    order_delivered_customer_date,
    order_estimated_delivery_date,
    customer_zip_code_prefix,
    customer_city,
    customer_state,
    customer_lat,
    customer_lng,
    has_payment_record,
    payment_total,
    payment_record_count,
    payment_method_count,
    primary_payment_type,
    max_payment_installments,
    is_multi_payment_record,
    is_multi_payment_method,
    has_item_record,
    item_count,
    distinct_product_count,
    distinct_seller_count,
    product_value,
    freight_value,
    order_value_including_freight,
    freight_ratio,
    average_item_price,
    total_weight_g,
    total_volume_cm3,
    missing_product_attribute_items,
    earliest_shipping_limit_date,
    latest_shipping_limit_date,
    primary_category_name,
    has_review_record,
    selected_review_id,
    review_score,
    is_low_score,
    is_strict_negative_score,
    has_review_text,
    review_response_hours,
    payment_approval_hours,
    handover_hours,
    shipping_hours,
    total_fulfillment_hours,
    is_delivery_analysis_eligible,
    has_complete_fulfillment_time,
    delivery_variance_days,
    is_late_delivery,
    late_days,
    early_days
)
SELECT o.order_id,
       o.customer_id,
       c.customer_unique_id,
       o.order_status,
       o.order_purchase_timestamp,
       o.order_approved_at,
       o.order_delivered_carrier_date,
       o.order_delivered_customer_date,
       o.order_estimated_delivery_date,
       c.customer_zip_code_prefix,
       c.customer_city,
       c.customer_state,
       g.geolocation_lat,
       g.geolocation_lng,
       CASE WHEN p.order_id IS NOT NULL THEN 1 ELSE 0 END,
       p.payment_total,
       p.payment_record_count,
       p.payment_method_count,
       p.primary_payment_type,
       p.max_payment_installments,
       p.is_multi_payment_record,
       p.is_multi_payment_method,
       CASE WHEN i.order_id IS NOT NULL THEN 1 ELSE 0 END,
       i.item_count,
       i.distinct_product_count,
       i.distinct_seller_count,
       i.product_value,
       i.freight_value,
       i.order_value_including_freight,
       i.freight_ratio,
       i.average_item_price,
       i.total_weight_g,
       i.total_volume_cm3,
       i.missing_product_attribute_items,
       i.earliest_shipping_limit_date,
       i.latest_shipping_limit_date,
       i.primary_category_name,
       CASE WHEN r.order_id IS NOT NULL THEN 1 ELSE 0 END,
       r.selected_review_id,
       r.review_score,
       r.is_low_score,
       r.is_strict_negative_score,
       r.has_review_text,
       r.review_response_hours,
       CASE
           WHEN o.order_approved_at IS NOT NULL
           THEN CAST(TIMESTAMPDIFF(SECOND, o.order_purchase_timestamp, o.order_approved_at) / 3600.0
                     AS DECIMAL(14,2))
           ELSE NULL
       END,
       CASE
           WHEN o.order_approved_at IS NOT NULL
            AND o.order_delivered_carrier_date IS NOT NULL
           THEN CAST(TIMESTAMPDIFF(SECOND, o.order_approved_at, o.order_delivered_carrier_date) / 3600.0
                     AS DECIMAL(14,2))
           ELSE NULL
       END,
       CASE
           WHEN o.order_delivered_carrier_date IS NOT NULL
            AND o.order_delivered_customer_date IS NOT NULL
           THEN CAST(TIMESTAMPDIFF(SECOND, o.order_delivered_carrier_date, o.order_delivered_customer_date) / 3600.0
                     AS DECIMAL(14,2))
           ELSE NULL
       END,
       CASE
           WHEN o.order_delivered_customer_date IS NOT NULL
           THEN CAST(TIMESTAMPDIFF(SECOND, o.order_purchase_timestamp, o.order_delivered_customer_date) / 3600.0
                     AS DECIMAL(14,2))
           ELSE NULL
       END,
       CASE
           WHEN o.order_status = 'delivered'
            AND o.order_delivered_customer_date IS NOT NULL
            AND o.order_estimated_delivery_date IS NOT NULL
           THEN 1 ELSE 0
       END,
       CASE
           WHEN o.order_approved_at IS NOT NULL
            AND o.order_delivered_carrier_date IS NOT NULL
            AND o.order_delivered_customer_date IS NOT NULL
            AND o.order_estimated_delivery_date IS NOT NULL
           THEN 1 ELSE 0
       END,
       CASE
           WHEN o.order_delivered_customer_date IS NOT NULL
            AND o.order_estimated_delivery_date IS NOT NULL
           THEN DATEDIFF(DATE(o.order_delivered_customer_date),
                         DATE(o.order_estimated_delivery_date))
           ELSE NULL
       END,
       CASE
           WHEN o.order_status = 'delivered'
            AND o.order_delivered_customer_date IS NOT NULL
            AND o.order_estimated_delivery_date IS NOT NULL
           THEN CASE
                    WHEN DATE(o.order_delivered_customer_date) > DATE(o.order_estimated_delivery_date)
                    THEN 1 ELSE 0
                END
           ELSE NULL
       END,
       CASE
           WHEN o.order_status = 'delivered'
            AND o.order_delivered_customer_date IS NOT NULL
            AND o.order_estimated_delivery_date IS NOT NULL
           THEN GREATEST(
                    DATEDIFF(DATE(o.order_delivered_customer_date),
                             DATE(o.order_estimated_delivery_date)),
                    0
                )
           ELSE NULL
       END,
       CASE
           WHEN o.order_status = 'delivered'
            AND o.order_delivered_customer_date IS NOT NULL
            AND o.order_estimated_delivery_date IS NOT NULL
           THEN GREATEST(
                    -DATEDIFF(DATE(o.order_delivered_customer_date),
                              DATE(o.order_estimated_delivery_date)),
                    0
                )
           ELSE NULL
       END
FROM raw_orders o
JOIN raw_customers c
  ON o.customer_id = c.customer_id
LEFT JOIN stg_geolocation_zip g
  ON c.customer_zip_code_prefix = g.zip_code_prefix
LEFT JOIN stg_order_payments p
  ON o.order_id = p.order_id
LEFT JOIN stg_order_items_summary i
  ON o.order_id = i.order_id
LEFT JOIN stg_order_reviews r
  ON o.order_id = r.order_id;

-- ============================================================
-- 2. 订单-卖家履约分析表：一行代表一笔订单中的一个卖家
-- 注意：交给承运方的时间来自订单表，并非卖家独立扫描时间；
-- 多卖家订单的卖家归因只能作为运营线索，不能解释为严格因果。
-- ============================================================
CREATE TABLE mart_order_seller_delivery (
    order_id                           CHAR(32)       NOT NULL,
    seller_id                          CHAR(32)       NOT NULL,
    order_status                       VARCHAR(20)    NOT NULL,
    order_purchase_timestamp           DATETIME       NOT NULL,
    customer_unique_id                 CHAR(32)       NOT NULL,

    customer_zip_code_prefix           CHAR(5)        NOT NULL,
    customer_city                      VARCHAR(120)   NOT NULL,
    customer_state                     CHAR(2)        NOT NULL,
    customer_lat                       DECIMAL(23,20) NULL,
    customer_lng                       DECIMAL(23,20) NULL,
    seller_zip_code_prefix             CHAR(5)        NOT NULL,
    seller_city                        VARCHAR(120)   NOT NULL,
    seller_state                       CHAR(2)        NOT NULL,
    seller_lat                         DECIMAL(23,20) NULL,
    seller_lng                         DECIMAL(23,20) NULL,
    is_cross_state                     TINYINT        NOT NULL,
    approximate_distance_km            DECIMAL(12,2)  NULL,

    seller_item_count                  INT            NOT NULL,
    seller_distinct_product_count      INT            NOT NULL,
    seller_product_value               DECIMAL(14,2)  NOT NULL,
    seller_freight_value               DECIMAL(14,2)  NOT NULL,
    seller_value_including_freight     DECIMAL(14,2)  NOT NULL,
    seller_freight_ratio               DECIMAL(10,6)  NOT NULL,
    seller_total_weight_g              DECIMAL(18,2)  NULL,
    seller_total_volume_cm3            DECIMAL(20,2)  NULL,
    missing_product_attribute_items    INT            NOT NULL,
    primary_category_name              VARCHAR(100)   NOT NULL,

    earliest_shipping_limit_date       DATETIME       NOT NULL,
    latest_shipping_limit_date         DATETIME       NOT NULL,
    order_delivered_carrier_date       DATETIME       NULL,
    handover_vs_earliest_limit_hours   DECIMAL(14,2)  NULL,
    handover_vs_latest_limit_hours     DECIMAL(14,2)  NULL,
    is_any_item_handover_late          TINYINT        NULL,
    is_all_items_handover_late         TINYINT        NULL,

    order_delivered_customer_date      DATETIME       NULL,
    order_estimated_delivery_date      DATETIME       NOT NULL,
    is_delivery_analysis_eligible      TINYINT        NOT NULL,
    delivery_variance_days             INT            NULL,
    is_late_delivery                   TINYINT        NULL,
    review_score                       TINYINT        NULL,
    is_low_score                       TINYINT        NULL,
    is_strict_negative_score           TINYINT        NULL,
    order_distinct_seller_count        INT            NOT NULL,
    is_multi_seller_order              TINYINT        NOT NULL,

    PRIMARY KEY (order_id, seller_id),
    KEY idx_mart_seller (seller_id),
    KEY idx_mart_seller_state (seller_state),
    KEY idx_mart_seller_customer_state (seller_state, customer_state),
    KEY idx_mart_seller_category (primary_category_name),
    KEY idx_mart_seller_late (is_any_item_handover_late, is_late_delivery),
    KEY idx_mart_seller_score (is_low_score)
) ENGINE = InnoDB;

INSERT INTO mart_order_seller_delivery (
    order_id,
    seller_id,
    order_status,
    order_purchase_timestamp,
    customer_unique_id,
    customer_zip_code_prefix,
    customer_city,
    customer_state,
    customer_lat,
    customer_lng,
    seller_zip_code_prefix,
    seller_city,
    seller_state,
    seller_lat,
    seller_lng,
    is_cross_state,
    approximate_distance_km,
    seller_item_count,
    seller_distinct_product_count,
    seller_product_value,
    seller_freight_value,
    seller_value_including_freight,
    seller_freight_ratio,
    seller_total_weight_g,
    seller_total_volume_cm3,
    missing_product_attribute_items,
    primary_category_name,
    earliest_shipping_limit_date,
    latest_shipping_limit_date,
    order_delivered_carrier_date,
    handover_vs_earliest_limit_hours,
    handover_vs_latest_limit_hours,
    is_any_item_handover_late,
    is_all_items_handover_late,
    order_delivered_customer_date,
    order_estimated_delivery_date,
    is_delivery_analysis_eligible,
    delivery_variance_days,
    is_late_delivery,
    review_score,
    is_low_score,
    is_strict_negative_score,
    order_distinct_seller_count,
    is_multi_seller_order
)
WITH seller_item_enriched AS (
    SELECT i.order_id,
           i.seller_id,
           i.order_item_id,
           i.product_id,
           i.shipping_limit_date,
           i.price,
           i.freight_value,
           p.product_weight_g,
           CASE
               WHEN p.product_length_cm IS NOT NULL
                AND p.product_height_cm IS NOT NULL
                AND p.product_width_cm IS NOT NULL
               THEN CAST(p.product_length_cm AS DECIMAL(20,2))
                    * p.product_height_cm * p.product_width_cm
               ELSE NULL
           END AS product_volume_cm3,
           CASE
               WHEN p.product_id IS NULL
                 OR p.product_weight_g IS NULL
                 OR p.product_length_cm IS NULL
                 OR p.product_height_cm IS NULL
                 OR p.product_width_cm IS NULL
               THEN 1 ELSE 0
           END AS has_missing_product_attribute,
           COALESCE(t.product_category_name_english,
                    p.product_category_name,
                    'unknown') AS category_name
    FROM raw_order_items i
    LEFT JOIN raw_products p
      ON i.product_id = p.product_id
    LEFT JOIN raw_category_translation t
      ON p.product_category_name = t.product_category_name
),
seller_category_value AS (
    SELECT order_id,
           seller_id,
           category_name,
           SUM(price) AS category_product_value
    FROM seller_item_enriched
    GROUP BY order_id, seller_id, category_name
),
ranked_seller_category AS (
    SELECT order_id,
           seller_id,
           category_name,
           ROW_NUMBER() OVER (
               PARTITION BY order_id, seller_id
               ORDER BY category_product_value DESC, category_name
           ) AS category_rank
    FROM seller_category_value
),
seller_item_summary AS (
    SELECT order_id,
           seller_id,
           COUNT(*) AS seller_item_count,
           COUNT(DISTINCT product_id) AS seller_distinct_product_count,
           CAST(SUM(price) AS DECIMAL(14,2)) AS seller_product_value,
           CAST(SUM(freight_value) AS DECIMAL(14,2)) AS seller_freight_value,
           CAST(SUM(price + freight_value) AS DECIMAL(14,2))
               AS seller_value_including_freight,
           CAST(SUM(freight_value) / NULLIF(SUM(price + freight_value), 0)
               AS DECIMAL(10,6)) AS seller_freight_ratio,
           CAST(SUM(product_weight_g) AS DECIMAL(18,2)) AS seller_total_weight_g,
           CAST(SUM(product_volume_cm3) AS DECIMAL(20,2)) AS seller_total_volume_cm3,
           SUM(has_missing_product_attribute) AS missing_product_attribute_items,
           MIN(shipping_limit_date) AS earliest_shipping_limit_date,
           MAX(shipping_limit_date) AS latest_shipping_limit_date
    FROM seller_item_enriched
    GROUP BY order_id, seller_id
)
SELECT ssum.order_id,
       ssum.seller_id,
       od.order_status,
       od.order_purchase_timestamp,
       od.customer_unique_id,
       od.customer_zip_code_prefix,
       od.customer_city,
       od.customer_state,
       od.customer_lat,
       od.customer_lng,
       s.seller_zip_code_prefix,
       s.seller_city,
       s.seller_state,
       sg.geolocation_lat,
       sg.geolocation_lng,
       CASE WHEN s.seller_state <> od.customer_state THEN 1 ELSE 0 END,
       CASE
           WHEN od.customer_lat IS NOT NULL
            AND od.customer_lng IS NOT NULL
            AND sg.geolocation_lat IS NOT NULL
            AND sg.geolocation_lng IS NOT NULL
           THEN CAST(
               6371.0 * 2 * ASIN(
                   SQRT(
                       LEAST(
                           1.0,
                           POWER(SIN(RADIANS(od.customer_lat - sg.geolocation_lat) / 2), 2)
                           + COS(RADIANS(sg.geolocation_lat))
                           * COS(RADIANS(od.customer_lat))
                           * POWER(SIN(RADIANS(od.customer_lng - sg.geolocation_lng) / 2), 2)
                       )
                   )
               ) AS DECIMAL(12,2)
           )
           ELSE NULL
       END,
       ssum.seller_item_count,
       ssum.seller_distinct_product_count,
       ssum.seller_product_value,
       ssum.seller_freight_value,
       ssum.seller_value_including_freight,
       ssum.seller_freight_ratio,
       ssum.seller_total_weight_g,
       ssum.seller_total_volume_cm3,
       ssum.missing_product_attribute_items,
       scat.category_name,
       ssum.earliest_shipping_limit_date,
       ssum.latest_shipping_limit_date,
       od.order_delivered_carrier_date,
       CASE
           WHEN od.order_delivered_carrier_date IS NOT NULL
           THEN CAST(TIMESTAMPDIFF(SECOND,
                                  ssum.earliest_shipping_limit_date,
                                  od.order_delivered_carrier_date) / 3600.0
                     AS DECIMAL(14,2))
           ELSE NULL
       END,
       CASE
           WHEN od.order_delivered_carrier_date IS NOT NULL
           THEN CAST(TIMESTAMPDIFF(SECOND,
                                  ssum.latest_shipping_limit_date,
                                  od.order_delivered_carrier_date) / 3600.0
                     AS DECIMAL(14,2))
           ELSE NULL
       END,
       CASE
           WHEN od.order_delivered_carrier_date IS NOT NULL
           THEN CASE
                    WHEN od.order_delivered_carrier_date > ssum.earliest_shipping_limit_date
                    THEN 1 ELSE 0
                END
           ELSE NULL
       END,
       CASE
           WHEN od.order_delivered_carrier_date IS NOT NULL
           THEN CASE
                    WHEN od.order_delivered_carrier_date > ssum.latest_shipping_limit_date
                    THEN 1 ELSE 0
                END
           ELSE NULL
       END,
       od.order_delivered_customer_date,
       od.order_estimated_delivery_date,
       od.is_delivery_analysis_eligible,
       od.delivery_variance_days,
       od.is_late_delivery,
       od.review_score,
       od.is_low_score,
       od.is_strict_negative_score,
       oi.distinct_seller_count,
       CASE WHEN oi.distinct_seller_count > 1 THEN 1 ELSE 0 END
FROM seller_item_summary ssum
JOIN ranked_seller_category scat
  ON ssum.order_id = scat.order_id
 AND ssum.seller_id = scat.seller_id
 AND scat.category_rank = 1
JOIN raw_sellers s
  ON ssum.seller_id = s.seller_id
JOIN mart_order_delivery od
  ON ssum.order_id = od.order_id
JOIN stg_order_items_summary oi
  ON ssum.order_id = oi.order_id
LEFT JOIN stg_geolocation_zip sg
  ON s.seller_zip_code_prefix = sg.zip_code_prefix;

-- 构建结果概览。
SELECT 'mart_order_delivery' AS table_name, COUNT(*) AS row_count
FROM mart_order_delivery
UNION ALL
SELECT 'mart_order_seller_delivery', COUNT(*)
FROM mart_order_seller_delivery;