-- 003_materialize_curated_tables.sql
--
-- Purpose:
-- Materialize the three main curated views into physical serving tables.
--
-- Principle:
-- Curated views define business rules.
-- Serving tables store the computed results for fast repeated queries.
--
-- This migration does not modify raw tables or curated views.

SET NAMES utf8mb4;

SELECT '[START] Materializing curated data into serving tables.' AS progress;


-- ============================================================
-- 1. Order-level fulfillment table
-- One row represents one order.
-- ============================================================

SELECT '[1/3] Building serving_order_fulfillment...' AS progress;

DROP TABLE IF EXISTS serving_order_fulfillment;

CREATE TABLE serving_order_fulfillment
ENGINE = InnoDB
AS
SELECT *
FROM curated_order_fulfillment;

ALTER TABLE serving_order_fulfillment
    ADD PRIMARY KEY (order_id);

SELECT
    '[PASS] serving_order_fulfillment created.' AS result,
    COUNT(*) AS row_count,
    COUNT(DISTINCT order_id) AS distinct_key_count
FROM serving_order_fulfillment;


-- ============================================================
-- 2. Order-item-level detail table
-- One row represents one item within an order.
-- ============================================================

SELECT '[2/3] Building serving_order_item_detail...' AS progress;

DROP TABLE IF EXISTS serving_order_item_detail;

CREATE TABLE serving_order_item_detail
ENGINE = InnoDB
AS
SELECT *
FROM curated_order_item_detail;

ALTER TABLE serving_order_item_detail
    ADD PRIMARY KEY (order_id, order_item_id);

SELECT
    '[PASS] serving_order_item_detail created.' AS result,
    COUNT(*) AS row_count,
    COUNT(DISTINCT order_id, order_item_id) AS distinct_key_count
FROM serving_order_item_detail;


-- ============================================================
-- 3. Review corpus table for later embedding and RAG
-- One row represents one review containing real text.
-- ============================================================

SELECT '[3/3] Building serving_review_corpus...' AS progress;

DROP TABLE IF EXISTS serving_review_corpus;

CREATE TABLE serving_review_corpus
ENGINE = InnoDB
AS
SELECT *
FROM curated_review_corpus;

ALTER TABLE serving_review_corpus
    ADD PRIMARY KEY (review_row_id);

SELECT
    '[PASS] serving_review_corpus created.' AS result,
    COUNT(*) AS row_count,
    COUNT(DISTINCT review_row_id) AS distinct_key_count
FROM serving_review_corpus;


SELECT '[PASS] All three serving tables were materialized successfully.' AS progress;

--执行迁移文件:
--Get-Content -Raw .\database\migrations\003_materialize_curated_tables.sql | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"'
--创建完成后检查:
--"SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'serving_%' ORDER BY TABLE_NAME;" | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"'
--
--测量实体表查询时间:
--(Measure-Command { "SELECT COUNT(*) FROM serving_order_fulfillment; SELECT COUNT(*) FROM serving_order_item_detail; SELECT COUNT(*) FROM serving_review_corpus;" | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"' }).TotalMilliseconds
