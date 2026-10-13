-- Migration 001: create all raw Olist data tables.
-- Raw tables preserve source-level records before business cleaning.
-- Business relationships are checked later in the curated layer.


-- =========================================================
-- 1. Customers
-- Source: olist_customers_dataset.csv
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_customers
(
    customer_id              CHAR(32)     NOT NULL,
    customer_unique_id       CHAR(32)     NOT NULL,
    customer_zip_code_prefix VARCHAR(5)   NOT NULL,
    customer_city            VARCHAR(255) NOT NULL,
    customer_state           CHAR(2)      NOT NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (customer_id),

    INDEX idx_raw_customers_unique_id (customer_unique_id),
    INDEX idx_raw_customers_zip_prefix (customer_zip_code_prefix)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from olist_customers_dataset.csv';


-- =========================================================
-- 2. Orders
-- Source: olist_orders_dataset.csv
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_orders
(
    order_id                      CHAR(32)    NOT NULL,
    customer_id                   CHAR(32)    NOT NULL,
    order_status                  VARCHAR(32) NOT NULL,
    order_purchase_timestamp      DATETIME    NOT NULL,
    order_approved_at             DATETIME    NULL,
    order_delivered_carrier_date  DATETIME    NULL,
    order_delivered_customer_date DATETIME    NULL,
    order_estimated_delivery_date DATETIME    NOT NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (order_id),

    INDEX idx_raw_orders_customer_id (customer_id),
    INDEX idx_raw_orders_status (order_status),
    INDEX idx_raw_orders_purchase_time (order_purchase_timestamp),
    INDEX idx_raw_orders_delivery_time (order_delivered_customer_date)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from olist_orders_dataset.csv';


-- =========================================================
-- 3. Order items
-- Source: olist_order_items_dataset.csv
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_order_items
(
    order_id            CHAR(32)      NOT NULL,
    order_item_id       INT UNSIGNED  NOT NULL,
    product_id          CHAR(32)      NOT NULL,
    seller_id           CHAR(32)      NOT NULL,
    shipping_limit_date DATETIME      NOT NULL,
    price                DECIMAL(12,2) NOT NULL,
    freight_value        DECIMAL(12,2) NOT NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (order_id, order_item_id),

    INDEX idx_raw_order_items_product_id (product_id),
    INDEX idx_raw_order_items_seller_id (seller_id),
    INDEX idx_raw_order_items_shipping_limit (shipping_limit_date)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from olist_order_items_dataset.csv';


-- =========================================================
-- 4. Order payments
-- Source: olist_order_payments_dataset.csv
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_order_payments
(
    order_id             CHAR(32)          NOT NULL,
    payment_sequential   SMALLINT UNSIGNED NOT NULL,
    payment_type         VARCHAR(32)       NOT NULL,
    payment_installments SMALLINT UNSIGNED NOT NULL,
    payment_value        DECIMAL(12,2)     NOT NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (order_id, payment_sequential),

    INDEX idx_raw_order_payments_type (payment_type)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from olist_order_payments_dataset.csv';


-- =========================================================
-- 5. Order reviews
-- Source: olist_order_reviews_dataset.csv
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_order_reviews
(
    review_row_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    review_id              CHAR(32)        NOT NULL,
    order_id               CHAR(32)        NOT NULL,
    review_score           TINYINT UNSIGNED NOT NULL,
    review_comment_title   VARCHAR(255)     NULL,
    review_comment_message TEXT             NULL,
    review_creation_date   DATETIME         NOT NULL,
    review_answer_timestamp DATETIME        NOT NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (review_row_id),

    INDEX idx_raw_order_reviews_review_id (review_id),
    INDEX idx_raw_order_reviews_order_id (order_id),
    INDEX idx_raw_order_reviews_score (review_score)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from olist_order_reviews_dataset.csv';


-- =========================================================
-- 6. Products
-- Source: olist_products_dataset.csv
-- The original source spelling "lenght" is intentionally retained.
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_products
(
    product_id                 CHAR(32)          NOT NULL,
    product_category_name      VARCHAR(255)      NULL,
    product_name_lenght        SMALLINT UNSIGNED NULL,
    product_description_lenght INT UNSIGNED      NULL,
    product_photos_qty         SMALLINT UNSIGNED NULL,
    product_weight_g           INT UNSIGNED      NULL,
    product_length_cm          INT UNSIGNED      NULL,
    product_height_cm          INT UNSIGNED      NULL,
    product_width_cm           INT UNSIGNED      NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (product_id),

    INDEX idx_raw_products_category (product_category_name)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from olist_products_dataset.csv';


-- =========================================================
-- 7. Sellers
-- Source: olist_sellers_dataset.csv
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_sellers
(
    seller_id              CHAR(32)     NOT NULL,
    seller_zip_code_prefix VARCHAR(5)   NOT NULL,
    seller_city            VARCHAR(255) NOT NULL,
    seller_state           CHAR(2)      NOT NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (seller_id),

    INDEX idx_raw_sellers_zip_prefix (seller_zip_code_prefix)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from olist_sellers_dataset.csv';


-- =========================================================
-- 8. Product category translation
-- Source: product_category_name_translation.csv
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_category_translation
(
    product_category_name         VARCHAR(255) NOT NULL,
    product_category_name_english VARCHAR(255) NOT NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (product_category_name)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from product_category_name_translation.csv';


-- =========================================================
-- 9. Geolocation
-- Source: olist_geolocation_dataset.csv
-- Duplicate ZIP-code records are intentionally preserved.
-- =========================================================
CREATE TABLE IF NOT EXISTS raw_geolocation
(
    geolocation_row_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    geolocation_zip_code_prefix VARCHAR(5)       NOT NULL,
    geolocation_lat             DOUBLE           NOT NULL,
    geolocation_lng             DOUBLE           NOT NULL,
    geolocation_city            VARCHAR(255)     NOT NULL,
    geolocation_state           CHAR(2)          NOT NULL,

    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (geolocation_row_id),

    INDEX idx_raw_geolocation_zip_prefix (geolocation_zip_code_prefix),
    INDEX idx_raw_geolocation_state (geolocation_state)
)
    ENGINE = InnoDB
    DEFAULT CHARACTER SET = utf8mb4
    COLLATE = utf8mb4_0900_ai_ci
    COMMENT = 'Raw records imported from olist_geolocation_dataset.csv';

-- 执行迁移文件:
-- Get-Content -Raw .\database\migrations\001_create_raw_tables.sql | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"'
-- 创建完成后检查:
-- "SHOW TABLES;" | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"'