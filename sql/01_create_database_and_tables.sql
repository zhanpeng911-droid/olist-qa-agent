-- Olist 项目：创建数据库和原始数据层
-- 执行顺序：第 2 个
-- 设计原则：raw 表保留 CSV 原始字段名与业务粒度，不在导入阶段做业务聚合。

CREATE DATABASE IF NOT EXISTS olist_ecommerce
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE olist_ecommerce;

CREATE TABLE IF NOT EXISTS raw_customers (
    customer_id              CHAR(32)     NOT NULL,
    customer_unique_id       CHAR(32)     NOT NULL,
    customer_zip_code_prefix CHAR(5)      NOT NULL,
    customer_city            VARCHAR(120) NOT NULL,
    customer_state           CHAR(2)      NOT NULL,
    PRIMARY KEY (customer_id),
    KEY idx_customers_unique_id (customer_unique_id),
    KEY idx_customers_zip (customer_zip_code_prefix),
    KEY idx_customers_state (customer_state)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS raw_geolocation (
    geolocation_row_id          BIGINT      NOT NULL AUTO_INCREMENT,
    geolocation_zip_code_prefix CHAR(5)     NOT NULL,
    geolocation_lat             DECIMAL(23,20) NOT NULL,
    geolocation_lng             DECIMAL(23,20) NOT NULL,
    geolocation_city            VARCHAR(120) NOT NULL,
    geolocation_state           CHAR(2)      NOT NULL,
    PRIMARY KEY (geolocation_row_id),
    KEY idx_geolocation_zip (geolocation_zip_code_prefix),
    KEY idx_geolocation_state (geolocation_state)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS raw_order_items (
    order_id            CHAR(32)      NOT NULL,
    order_item_id       INT           NOT NULL,
    product_id          CHAR(32)      NOT NULL,
    seller_id           CHAR(32)      NOT NULL,
    shipping_limit_date DATETIME      NOT NULL,
    price               DECIMAL(12,2) NOT NULL,
    freight_value       DECIMAL(12,2) NOT NULL,
    PRIMARY KEY (order_id, order_item_id),
    KEY idx_order_items_product (product_id),
    KEY idx_order_items_seller (seller_id),
    KEY idx_order_items_shipping_limit (shipping_limit_date)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS raw_order_payments (
    order_id             CHAR(32)      NOT NULL,
    payment_sequential   INT           NOT NULL,
    payment_type         VARCHAR(30)   NOT NULL,
    payment_installments INT           NOT NULL,
    payment_value        DECIMAL(12,2) NOT NULL,
    PRIMARY KEY (order_id, payment_sequential),
    KEY idx_payments_type (payment_type)
) ENGINE = InnoDB;

-- review_id 和 order_id 在源数据中都不是绝对唯一，因此增加代理主键。
CREATE TABLE IF NOT EXISTS raw_order_reviews (
    review_row_id          BIGINT      NOT NULL AUTO_INCREMENT,
    review_id              CHAR(32)    NOT NULL,
    order_id               CHAR(32)    NOT NULL,
    review_score           TINYINT     NOT NULL,
    review_comment_title   TEXT        NULL,
    review_comment_message TEXT        NULL,
    review_creation_date   DATETIME    NOT NULL,
    review_answer_timestamp DATETIME   NOT NULL,
    PRIMARY KEY (review_row_id),
    KEY idx_reviews_review_id (review_id),
    KEY idx_reviews_order_id (order_id),
    KEY idx_reviews_score (review_score)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS raw_orders (
    order_id                       CHAR(32)    NOT NULL,
    customer_id                    CHAR(32)    NOT NULL,
    order_status                   VARCHAR(20) NOT NULL,
    order_purchase_timestamp       DATETIME    NOT NULL,
    order_approved_at              DATETIME    NULL,
    order_delivered_carrier_date   DATETIME    NULL,
    order_delivered_customer_date  DATETIME    NULL,
    order_estimated_delivery_date  DATETIME    NOT NULL,
    PRIMARY KEY (order_id),
    KEY idx_orders_customer (customer_id),
    KEY idx_orders_status (order_status),
    KEY idx_orders_purchase_time (order_purchase_timestamp)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS raw_products (
    product_id                 CHAR(32)     NOT NULL,
    product_category_name      VARCHAR(100) NULL,
    product_name_lenght        INT          NULL,
    product_description_lenght INT          NULL,
    product_photos_qty         INT          NULL,
    product_weight_g           INT          NULL,
    product_length_cm          INT          NULL,
    product_height_cm          INT          NULL,
    product_width_cm           INT          NULL,
    PRIMARY KEY (product_id),
    KEY idx_products_category (product_category_name)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS raw_sellers (
    seller_id              CHAR(32)     NOT NULL,
    seller_zip_code_prefix CHAR(5)      NOT NULL,
    seller_city            VARCHAR(120) NOT NULL,
    seller_state           CHAR(2)      NOT NULL,
    PRIMARY KEY (seller_id),
    KEY idx_sellers_zip (seller_zip_code_prefix),
    KEY idx_sellers_state (seller_state)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS raw_category_translation (
    product_category_name         VARCHAR(100) NOT NULL,
    product_category_name_english VARCHAR(100) NOT NULL,
    PRIMARY KEY (product_category_name)
) ENGINE = InnoDB;

SHOW TABLES;

