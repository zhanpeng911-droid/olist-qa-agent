-- Olist 项目：构建 staging 清洗聚合层
-- 执行顺序：原始层质量检查通过后执行。
-- 本脚本只删除并重建 stg_ 表，不修改任何 raw_ 原始表。

USE olist_ecommerce;
SET NAMES utf8mb4;

-- ============================================================
-- 1. 邮编地理表：一行一个邮编前缀
-- 同一邮编在原始表中可能有多条坐标和城市写法。
-- 经纬度取均值；城市/州取该邮编下出现频次最高的组合。
-- ============================================================
DROP TABLE IF EXISTS stg_geolocation_zip;

CREATE TABLE stg_geolocation_zip (
    zip_code_prefix        CHAR(5)        NOT NULL,
    geolocation_lat        DECIMAL(23,20) NOT NULL,
    geolocation_lng        DECIMAL(23,20) NOT NULL,
    geolocation_city       VARCHAR(120)   NOT NULL,
    geolocation_state      CHAR(2)        NOT NULL,
    source_row_count       INT            NOT NULL,
    city_state_variant_count INT          NOT NULL,
    PRIMARY KEY (zip_code_prefix),
    KEY idx_stg_geo_state (geolocation_state)
) ENGINE = InnoDB;

INSERT INTO stg_geolocation_zip (
    zip_code_prefix,
    geolocation_lat,
    geolocation_lng,
    geolocation_city,
    geolocation_state,
    source_row_count,
    city_state_variant_count
)
WITH location_counts AS (
    SELECT geolocation_zip_code_prefix,
           geolocation_city,
           geolocation_state,
           COUNT(*) AS location_frequency
    FROM raw_geolocation
    GROUP BY geolocation_zip_code_prefix, geolocation_city, geolocation_state
),
ranked_locations AS (
    SELECT geolocation_zip_code_prefix,
           geolocation_city,
           geolocation_state,
           ROW_NUMBER() OVER (
               PARTITION BY geolocation_zip_code_prefix
               ORDER BY location_frequency DESC, geolocation_state, geolocation_city
           ) AS location_rank
    FROM location_counts
),
coordinate_summary AS (
    SELECT geolocation_zip_code_prefix,
           CAST(AVG(geolocation_lat) AS DECIMAL(23,20)) AS average_lat,
           CAST(AVG(geolocation_lng) AS DECIMAL(23,20)) AS average_lng,
           COUNT(*) AS source_row_count,
           COUNT(DISTINCT CONCAT_WS('|', geolocation_city, geolocation_state))
               AS city_state_variant_count
    FROM raw_geolocation
    GROUP BY geolocation_zip_code_prefix
)
SELECT c.geolocation_zip_code_prefix,
       c.average_lat,
       c.average_lng,
       r.geolocation_city,
       r.geolocation_state,
       c.source_row_count,
       c.city_state_variant_count
FROM coordinate_summary c
JOIN ranked_locations r
  ON c.geolocation_zip_code_prefix = r.geolocation_zip_code_prefix
 AND r.location_rank = 1;

-- ============================================================
-- 2. 订单支付汇总表：一行一笔订单
-- 主要支付方式按该方式在订单中的支付金额最高确定；并列时按名称排序。
-- ============================================================
DROP TABLE IF EXISTS stg_order_payments;

CREATE TABLE stg_order_payments (
    order_id                   CHAR(32)      NOT NULL,
    payment_total              DECIMAL(14,2) NOT NULL,
    payment_record_count       INT           NOT NULL,
    payment_method_count       INT           NOT NULL,
    primary_payment_type       VARCHAR(30)   NOT NULL,
    max_payment_installments   INT           NOT NULL,
    is_multi_payment_record    TINYINT    NOT NULL,
    is_multi_payment_method    TINYINT    NOT NULL,
    PRIMARY KEY (order_id),
    KEY idx_stg_payment_type (primary_payment_type)
) ENGINE = InnoDB;

INSERT INTO stg_order_payments (
    order_id,
    payment_total,
    payment_record_count,
    payment_method_count,
    primary_payment_type,
    max_payment_installments,
    is_multi_payment_record,
    is_multi_payment_method
)
WITH payment_by_type AS (
    SELECT order_id,
           payment_type,
           SUM(payment_value) AS type_payment_total
    FROM raw_order_payments
    GROUP BY order_id, payment_type
),
ranked_payment_type AS (
    SELECT order_id,
           payment_type,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY type_payment_total DESC, payment_type
           ) AS payment_type_rank
    FROM payment_by_type
),
payment_summary AS (
    SELECT order_id,
           CAST(SUM(payment_value) AS DECIMAL(14,2)) AS payment_total,
           COUNT(*) AS payment_record_count,
           COUNT(DISTINCT payment_type) AS payment_method_count,
           MAX(payment_installments) AS max_payment_installments
    FROM raw_order_payments
    GROUP BY order_id
)
SELECT s.order_id,
       s.payment_total,
       s.payment_record_count,
       s.payment_method_count,
       r.payment_type,
       s.max_payment_installments,
       CASE WHEN s.payment_record_count > 1 THEN 1 ELSE 0 END,
       CASE WHEN s.payment_method_count > 1 THEN 1 ELSE 0 END
FROM payment_summary s
JOIN ranked_payment_type r
  ON s.order_id = r.order_id
 AND r.payment_type_rank = 1;

-- ============================================================
-- 3. 订单评价汇总表：一行一笔订单
-- 一单多评时保留回答时间最晚的一条，视为客户最终评价。
-- 主口径：1-3 分为低评分；同时保留 1-2 分严格负面口径用于敏感性分析。
-- ============================================================
DROP TABLE IF EXISTS stg_order_reviews;

CREATE TABLE stg_order_reviews (
    order_id                 CHAR(32)      NOT NULL,
    selected_review_id       CHAR(32)      NOT NULL,
    review_score             TINYINT       NOT NULL,
    is_low_score             TINYINT    NOT NULL COMMENT '主口径：review_score <= 3',
    is_strict_negative_score TINYINT    NOT NULL COMMENT '对照口径：review_score <= 2',
    review_comment_title     TEXT           NULL,
    review_comment_message   TEXT           NULL,
    has_review_text          TINYINT    NOT NULL,
    review_creation_date     DATETIME      NOT NULL,
    review_answer_timestamp  DATETIME      NOT NULL,
    review_response_hours    DECIMAL(12,2) NOT NULL,
    review_record_count      INT           NOT NULL,
    PRIMARY KEY (order_id),
    KEY idx_stg_review_score (review_score),
    KEY idx_stg_review_low_score (is_low_score)
) ENGINE = InnoDB;

INSERT INTO stg_order_reviews (
    order_id,
    selected_review_id,
    review_score,
    is_low_score,
    is_strict_negative_score,
    review_comment_title,
    review_comment_message,
    has_review_text,
    review_creation_date,
    review_answer_timestamp,
    review_response_hours,
    review_record_count
)
WITH ranked_reviews AS (
    SELECT review_row_id,
           review_id,
           order_id,
           review_score,
           review_comment_title,
           review_comment_message,
           review_creation_date,
           review_answer_timestamp,
           COUNT(*) OVER (PARTITION BY order_id) AS review_record_count,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY review_answer_timestamp DESC,
                        review_creation_date DESC,
                        review_row_id DESC
           ) AS review_rank
    FROM raw_order_reviews
)
SELECT order_id,
       review_id,
       review_score,
       CASE WHEN review_score <= 3 THEN 1 ELSE 0 END,
       CASE WHEN review_score <= 2 THEN 1 ELSE 0 END,
       review_comment_title,
       review_comment_message,
       CASE
           WHEN review_comment_title IS NOT NULL OR review_comment_message IS NOT NULL THEN 1
           ELSE 0
       END,
       review_creation_date,
       review_answer_timestamp,
       CAST(TIMESTAMPDIFF(SECOND, review_creation_date, review_answer_timestamp) / 3600.0
            AS DECIMAL(12,2)),
       review_record_count
FROM ranked_reviews
WHERE review_rank = 1;

-- ============================================================
-- 4. 订单商品汇总表：一行一笔订单
-- 商品金额和运费均从订单商品明细汇总，避免与支付/评价多行连接时金额膨胀。
-- 主要品类按订单内商品金额最高确定；并列时按品类名称排序。
-- ============================================================
DROP TABLE IF EXISTS stg_order_items_summary;

CREATE TABLE stg_order_items_summary (
    order_id                           CHAR(32)       NOT NULL,
    item_count                         INT            NOT NULL,
    distinct_product_count             INT            NOT NULL,
    distinct_seller_count              INT            NOT NULL,
    product_value                      DECIMAL(14,2)  NOT NULL,
    freight_value                      DECIMAL(14,2)  NOT NULL,
    order_value_including_freight      DECIMAL(14,2)  NOT NULL,
    freight_ratio                      DECIMAL(10,6)  NOT NULL,
    average_item_price                 DECIMAL(14,2)  NOT NULL,
    total_weight_g                     DECIMAL(18,2)  NULL,
    total_volume_cm3                   DECIMAL(20,2)  NULL,
    missing_product_attribute_items    INT            NOT NULL,
    earliest_shipping_limit_date       DATETIME       NOT NULL,
    latest_shipping_limit_date         DATETIME       NOT NULL,
    primary_category_name              VARCHAR(100)   NOT NULL,
    PRIMARY KEY (order_id),
    KEY idx_stg_items_category (primary_category_name)
) ENGINE = InnoDB;

INSERT INTO stg_order_items_summary (
    order_id,
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
    primary_category_name
)
WITH item_enriched AS (
    SELECT i.order_id,
           i.order_item_id,
           i.product_id,
           i.seller_id,
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
category_value AS (
    SELECT order_id,
           category_name,
           SUM(price) AS category_product_value
    FROM item_enriched
    GROUP BY order_id, category_name
),
ranked_category AS (
    SELECT order_id,
           category_name,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY category_product_value DESC, category_name
           ) AS category_rank
    FROM category_value
),
item_summary AS (
    SELECT order_id,
           COUNT(*) AS item_count,
           COUNT(DISTINCT product_id) AS distinct_product_count,
           COUNT(DISTINCT seller_id) AS distinct_seller_count,
           CAST(SUM(price) AS DECIMAL(14,2)) AS product_value,
           CAST(SUM(freight_value) AS DECIMAL(14,2)) AS freight_value,
           CAST(SUM(price + freight_value) AS DECIMAL(14,2))
               AS order_value_including_freight,
           CAST(SUM(freight_value) / NULLIF(SUM(price + freight_value), 0)
               AS DECIMAL(10,6)) AS freight_ratio,
           CAST(AVG(price) AS DECIMAL(14,2)) AS average_item_price,
           CAST(SUM(product_weight_g) AS DECIMAL(18,2)) AS total_weight_g,
           CAST(SUM(product_volume_cm3) AS DECIMAL(20,2)) AS total_volume_cm3,
           SUM(has_missing_product_attribute) AS missing_product_attribute_items,
           MIN(shipping_limit_date) AS earliest_shipping_limit_date,
           MAX(shipping_limit_date) AS latest_shipping_limit_date
    FROM item_enriched
    GROUP BY order_id
)
SELECT s.order_id,
       s.item_count,
       s.distinct_product_count,
       s.distinct_seller_count,
       s.product_value,
       s.freight_value,
       s.order_value_including_freight,
       s.freight_ratio,
       s.average_item_price,
       s.total_weight_g,
       s.total_volume_cm3,
       s.missing_product_attribute_items,
       s.earliest_shipping_limit_date,
       s.latest_shipping_limit_date,
       c.category_name
FROM item_summary s
JOIN ranked_category c
  ON s.order_id = c.order_id
 AND c.category_rank = 1;

-- 构建结果概览。
SELECT 'stg_geolocation_zip' AS table_name, COUNT(*) AS row_count FROM stg_geolocation_zip
UNION ALL SELECT 'stg_order_payments', COUNT(*) FROM stg_order_payments
UNION ALL SELECT 'stg_order_reviews', COUNT(*) FROM stg_order_reviews
UNION ALL SELECT 'stg_order_items_summary', COUNT(*) FROM stg_order_items_summary;