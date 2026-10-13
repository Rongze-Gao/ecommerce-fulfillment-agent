-- 004_add_order_fulfillment_indexes.sql
--
-- Purpose:
-- Add indexes required by the first group of order-level Agent tools.
--
-- This migration must be executed once after
-- 003_materialize_curated_tables.sql.

SET NAMES utf8mb4;

SELECT '[START] Adding indexes to serving_order_fulfillment.' AS progress;

ALTER TABLE serving_order_fulfillment
    ADD INDEX idx_sof_purchase_date (
        purchase_date
    ),
    ADD INDEX idx_sof_state_purchase_date (
        customer_state,
        purchase_date
    ),
    ADD INDEX idx_sof_late_purchase_date (
        is_late_delivery,
        purchase_date
    ),
    ADD INDEX idx_sof_anomaly_purchase_date (
        has_timeline_anomaly,
        purchase_date
    ),
    ADD INDEX idx_sof_status_purchase_date (
        order_status,
        purchase_date
    ),
    ADD INDEX idx_sof_customer_purchase_date (
        customer_unique_id,
        purchase_date
    );

SELECT '[PASS] Order-fulfillment indexes created.' AS progress;

ANALYZE TABLE serving_order_fulfillment;

SELECT
    INDEX_NAME,
    GROUP_CONCAT(
        COLUMN_NAME
        ORDER BY SEQ_IN_INDEX
        SEPARATOR ', '
    ) AS indexed_columns,
    NON_UNIQUE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'serving_order_fulfillment'
GROUP BY
    INDEX_NAME,
    NON_UNIQUE
ORDER BY
    INDEX_NAME;

SELECT '[PASS] Index creation and statistics update completed.' AS progress;

--执行迁移文件:
--Get-Content -Raw .\database\migrations\004_add_order_fulfillment_indexes.sql | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"'
--执行代表性业务查询计划:
--"EXPLAIN ANALYZE SELECT order_id, purchase_date, customer_state, order_status, is_late_delivery, delivery_days, actual_minus_estimated_days FROM serving_order_fulfillment WHERE is_late_delivery = 1 AND purchase_date >= '2018-01-01' AND purchase_date < '2018-07-01' ORDER BY purchase_date DESC LIMIT 20;" | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"'
