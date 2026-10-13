-- Migration 002: create curated analytical views.
--
-- These views do not modify raw data.
-- They provide stable business grains for SQL tools and the future Agent.


-- =========================================================
-- 1. One row per order: aggregated item information
-- =========================================================
CREATE OR REPLACE
    SQL SECURITY INVOKER
VIEW curated_order_item_summary AS
SELECT
    oi.order_id,

    COUNT(*) AS item_record_count,
    COUNT(DISTINCT oi.product_id) AS distinct_product_count,
    COUNT(DISTINCT oi.seller_id) AS distinct_seller_count,

    SUM(oi.price) AS total_item_value,
    SUM(oi.freight_value) AS total_freight_value,
    SUM(oi.price + oi.freight_value) AS total_order_value,

    MIN(oi.shipping_limit_date) AS earliest_shipping_limit,
    MAX(oi.shipping_limit_date) AS latest_shipping_limit

FROM raw_order_items AS oi
GROUP BY oi.order_id;


-- =========================================================
-- 2. One row per order: aggregated payment information
-- =========================================================
CREATE OR REPLACE
    SQL SECURITY INVOKER
VIEW curated_order_payment_summary AS
SELECT
    op.order_id,

    COUNT(*) AS payment_record_count,
    COUNT(DISTINCT op.payment_type) AS payment_type_count,

    GROUP_CONCAT(
        DISTINCT op.payment_type
        ORDER BY op.payment_type
        SEPARATOR ','
    ) AS payment_types,

    MAX(op.payment_installments) AS maximum_installments,
    SUM(op.payment_value) AS total_payment_value

FROM raw_order_payments AS op
GROUP BY op.order_id;


-- =========================================================
-- 3. One selected review per order
--
-- Rule:
--   1. Prefer the latest answer timestamp.
--   2. If timestamps are equal, prefer the larger raw row ID.
--
-- Raw reviews are not deleted. This view only determines which review
-- represents an order in one-row-per-order analysis.
-- =========================================================
CREATE OR REPLACE
    SQL SECURITY INVOKER
VIEW curated_order_latest_review AS
SELECT
    ranked_reviews.review_row_id,
    ranked_reviews.review_id,
    ranked_reviews.order_id,
    ranked_reviews.review_score,
    ranked_reviews.review_comment_title,
    ranked_reviews.review_comment_message,
    ranked_reviews.review_creation_date,
    ranked_reviews.review_answer_timestamp,
    ranked_reviews.review_row_count

FROM (
    SELECT
        r.review_row_id,
        r.review_id,
        r.order_id,
        r.review_score,
        r.review_comment_title,
        r.review_comment_message,
        r.review_creation_date,
        r.review_answer_timestamp,

        COUNT(*) OVER (
            PARTITION BY r.order_id
        ) AS review_row_count,

        ROW_NUMBER() OVER (
            PARTITION BY r.order_id
            ORDER BY
                r.review_answer_timestamp DESC,
                r.review_creation_date DESC,
                r.review_row_id DESC
        ) AS review_rank

    FROM raw_order_reviews AS r
) AS ranked_reviews

WHERE ranked_reviews.review_rank = 1;


-- =========================================================
-- 4. Main order-level fulfillment view
--
-- Grain: exactly one row per order.
-- This is the main structured-data source for the Agent.
-- =========================================================
CREATE OR REPLACE
    SQL SECURITY INVOKER
VIEW curated_order_fulfillment AS
SELECT
    -- Order identity
    o.order_id,
    o.order_status,

    -- Customer identity
    o.customer_id,
    c.customer_unique_id,
    c.customer_zip_code_prefix,
    c.customer_city,
    c.customer_state,

    -- Order timestamps
    o.order_purchase_timestamp,
    o.order_approved_at,
    o.order_delivered_carrier_date,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,

    DATE(o.order_purchase_timestamp) AS purchase_date,
    DATE_FORMAT(
        o.order_purchase_timestamp,
        '%Y-%m'
    ) AS purchase_month,

    -- Basic status flags
    CASE
        WHEN o.order_status = 'delivered'
        THEN 1
        ELSE 0
    END AS is_delivered,

    CASE
        WHEN o.order_status = 'canceled'
        THEN 1
        ELSE 0
    END AS is_canceled,

    CASE
        WHEN o.order_delivered_customer_date IS NOT NULL
        THEN 1
        ELSE 0
    END AS has_delivery_date,

    -- An order is eligible for end-to-end delivery metrics only
    -- when it is delivered and has a valid purchase-to-delivery order.
    CASE
        WHEN o.order_status = 'delivered'
         AND o.order_delivered_customer_date IS NOT NULL
         AND o.order_estimated_delivery_date IS NOT NULL
         AND o.order_delivered_customer_date
             >= o.order_purchase_timestamp
        THEN 1
        ELSE 0
    END AS is_delivery_metric_eligible,

    -- Late-delivery rule:
    -- Compare calendar dates, not exact timestamps.
    CASE
        WHEN o.order_status = 'delivered'
         AND o.order_delivered_customer_date IS NOT NULL
         AND o.order_estimated_delivery_date IS NOT NULL
         AND o.order_delivered_customer_date
             >= o.order_purchase_timestamp
        THEN
            CASE
                WHEN DATE(o.order_delivered_customer_date)
                     > DATE(o.order_estimated_delivery_date)
                THEN 1
                ELSE 0
            END
        ELSE NULL
    END AS is_late_delivery,

    -- End-to-end delivery duration
    CASE
        WHEN o.order_delivered_customer_date IS NOT NULL
         AND o.order_delivered_customer_date
             >= o.order_purchase_timestamp
        THEN ROUND(
            TIMESTAMPDIFF(
                MINUTE,
                o.order_purchase_timestamp,
                o.order_delivered_customer_date
            ) / 1440.0,
            2
        )
        ELSE NULL
    END AS delivery_days,

    -- Negative means delivered early; positive means delivered late.
    CASE
        WHEN o.order_delivered_customer_date IS NOT NULL
         AND o.order_estimated_delivery_date IS NOT NULL
        THEN DATEDIFF(
            DATE(o.order_delivered_customer_date),
            DATE(o.order_estimated_delivery_date)
        )
        ELSE NULL
    END AS actual_minus_estimated_days,

    -- Approval-to-carrier duration is only calculated when
    -- the two timestamps have a valid order.
    CASE
        WHEN o.order_approved_at IS NOT NULL
         AND o.order_delivered_carrier_date IS NOT NULL
         AND o.order_delivered_carrier_date
             >= o.order_approved_at
        THEN ROUND(
            TIMESTAMPDIFF(
                MINUTE,
                o.order_approved_at,
                o.order_delivered_carrier_date
            ) / 1440.0,
            2
        )
        ELSE NULL
    END AS approval_to_carrier_days,

    -- Carrier-to-customer duration
    CASE
        WHEN o.order_delivered_carrier_date IS NOT NULL
         AND o.order_delivered_customer_date IS NOT NULL
         AND o.order_delivered_customer_date
             >= o.order_delivered_carrier_date
        THEN ROUND(
            TIMESTAMPDIFF(
                MINUTE,
                o.order_delivered_carrier_date,
                o.order_delivered_customer_date
            ) / 1440.0,
            2
        )
        ELSE NULL
    END AS carrier_to_customer_days,

    -- Individual data-quality flags
    CASE
        WHEN o.order_approved_at IS NOT NULL
         AND o.order_approved_at
             < o.order_purchase_timestamp
        THEN 1
        ELSE 0
    END AS has_approval_before_purchase_anomaly,

    CASE
        WHEN o.order_approved_at IS NOT NULL
         AND o.order_delivered_carrier_date IS NOT NULL
         AND o.order_delivered_carrier_date
             < o.order_approved_at
        THEN 1
        ELSE 0
    END AS has_carrier_before_approval_anomaly,

    CASE
        WHEN o.order_delivered_carrier_date IS NOT NULL
         AND o.order_delivered_customer_date IS NOT NULL
         AND o.order_delivered_customer_date
             < o.order_delivered_carrier_date
        THEN 1
        ELSE 0
    END AS has_delivery_before_carrier_anomaly,

    CASE
        WHEN (
            o.order_status = 'delivered'
            AND o.order_delivered_customer_date IS NULL
        )
        OR (
            o.order_status <> 'delivered'
            AND o.order_delivered_customer_date IS NOT NULL
        )
        THEN 1
        ELSE 0
    END AS has_status_date_anomaly,

    CASE
        WHEN (
            o.order_approved_at IS NOT NULL
            AND o.order_approved_at
                < o.order_purchase_timestamp
        )
        OR (
            o.order_approved_at IS NOT NULL
            AND o.order_delivered_carrier_date IS NOT NULL
            AND o.order_delivered_carrier_date
                < o.order_approved_at
        )
        OR (
            o.order_delivered_carrier_date IS NOT NULL
            AND o.order_delivered_customer_date IS NOT NULL
            AND o.order_delivered_customer_date
                < o.order_delivered_carrier_date
        )
        OR (
            o.order_status = 'delivered'
            AND o.order_delivered_customer_date IS NULL
        )
        OR (
            o.order_status <> 'delivered'
            AND o.order_delivered_customer_date IS NOT NULL
        )
        THEN 1
        ELSE 0
    END AS has_timeline_anomaly,

    -- Item summary
    CASE
        WHEN item_summary.order_id IS NOT NULL
        THEN 1
        ELSE 0
    END AS has_item_records,

    COALESCE(
        item_summary.item_record_count,
        0
    ) AS item_record_count,

    COALESCE(
        item_summary.distinct_product_count,
        0
    ) AS distinct_product_count,

    COALESCE(
        item_summary.distinct_seller_count,
        0
    ) AS distinct_seller_count,

    COALESCE(
        item_summary.total_item_value,
        0.00
    ) AS total_item_value,

    COALESCE(
        item_summary.total_freight_value,
        0.00
    ) AS total_freight_value,

    COALESCE(
        item_summary.total_order_value,
        0.00
    ) AS total_order_value,

    item_summary.earliest_shipping_limit,
    item_summary.latest_shipping_limit,

    -- Payment summary
    CASE
        WHEN payment_summary.order_id IS NOT NULL
        THEN 1
        ELSE 0
    END AS has_payment_records,

    COALESCE(
        payment_summary.payment_record_count,
        0
    ) AS payment_record_count,

    COALESCE(
        payment_summary.payment_type_count,
        0
    ) AS payment_type_count,

    payment_summary.payment_types,
    payment_summary.maximum_installments,

    COALESCE(
        payment_summary.total_payment_value,
        0.00
    ) AS total_payment_value,

    CASE
        WHEN item_summary.order_id IS NOT NULL
         AND payment_summary.order_id IS NOT NULL
        THEN ROUND(
            payment_summary.total_payment_value
            - item_summary.total_order_value,
            2
        )
        ELSE NULL
    END AS payment_minus_order_value,

    -- Selected order-level review
    CASE
        WHEN latest_review.review_row_id IS NOT NULL
        THEN 1
        ELSE 0
    END AS has_review,

    latest_review.review_row_id
        AS selected_review_row_id,

    latest_review.review_id
        AS selected_review_id,

    latest_review.review_score,

    latest_review.review_comment_title,
    latest_review.review_comment_message,
    latest_review.review_creation_date,
    latest_review.review_answer_timestamp,

    COALESCE(
        latest_review.review_row_count,
        0
    ) AS review_row_count,

    CASE
        WHEN latest_review.review_comment_message IS NOT NULL
         AND TRIM(
             latest_review.review_comment_message
         ) <> ''
        THEN 1
        ELSE 0
    END AS has_review_message

FROM raw_orders AS o

INNER JOIN raw_customers AS c
    ON c.customer_id = o.customer_id

LEFT JOIN curated_order_item_summary AS item_summary
    ON item_summary.order_id = o.order_id

LEFT JOIN curated_order_payment_summary AS payment_summary
    ON payment_summary.order_id = o.order_id

LEFT JOIN curated_order_latest_review AS latest_review
    ON latest_review.order_id = o.order_id;


-- =========================================================
-- 5. Item-level fulfillment view
--
-- Grain: one row per order item.
-- Used for category, product and seller investigations.
-- =========================================================
CREATE OR REPLACE
    SQL SECURITY INVOKER
VIEW curated_order_item_detail AS
SELECT
    oi.order_id,
    oi.order_item_id,

    fulfillment.order_status,
    fulfillment.order_purchase_timestamp,
    fulfillment.purchase_date,
    fulfillment.purchase_month,

    fulfillment.customer_unique_id,
    fulfillment.customer_city,
    fulfillment.customer_state,

    fulfillment.is_delivery_metric_eligible,
    fulfillment.is_late_delivery,
    fulfillment.delivery_days,
    fulfillment.actual_minus_estimated_days,
    fulfillment.has_timeline_anomaly,

    oi.product_id,

    p.product_category_name
        AS product_category_name_original,

    COALESCE(
        category_translation.product_category_name_english,
        p.product_category_name,
        'unknown'
    ) AS product_category_name,

    p.product_weight_g,
    p.product_length_cm,
    p.product_height_cm,
    p.product_width_cm,

    oi.seller_id,
    s.seller_city,
    s.seller_state,

    oi.shipping_limit_date,
    oi.price,
    oi.freight_value,
    oi.price + oi.freight_value
        AS item_total_value,

    fulfillment.order_delivered_carrier_date,

    CASE
        WHEN fulfillment.order_delivered_carrier_date
             IS NOT NULL
        THEN DATEDIFF(
            DATE(
                fulfillment.order_delivered_carrier_date
            ),
            DATE(oi.shipping_limit_date)
        )
        ELSE NULL
    END AS carrier_handoff_minus_shipping_limit_days,

    CASE
        WHEN fulfillment.order_delivered_carrier_date
             IS NOT NULL
         AND DATE(
             fulfillment.order_delivered_carrier_date
         ) > DATE(oi.shipping_limit_date)
        THEN 1

        WHEN fulfillment.order_delivered_carrier_date
             IS NOT NULL
        THEN 0

        ELSE NULL
    END AS is_shipping_limit_missed,

    fulfillment.review_score

FROM raw_order_items AS oi

INNER JOIN curated_order_fulfillment AS fulfillment
    ON fulfillment.order_id = oi.order_id

INNER JOIN raw_products AS p
    ON p.product_id = oi.product_id

INNER JOIN raw_sellers AS s
    ON s.seller_id = oi.seller_id

LEFT JOIN raw_category_translation
    AS category_translation
    ON category_translation.product_category_name
       = p.product_category_name;


-- =========================================================
-- 6. Review corpus for the future RAG layer
--
-- Grain: one row per non-empty review message.
-- No synthetic text is created.
-- All original review rows remain available.
-- =========================================================
CREATE OR REPLACE
    SQL SECURITY INVOKER
VIEW curated_review_corpus AS
SELECT
    CONCAT(
        'olist-review-',
        r.review_row_id
    ) AS document_id,

    r.review_row_id,
    r.review_id,
    r.order_id,
    r.review_score,

    r.review_comment_title,
    r.review_comment_message,

    TRIM(
        CONCAT_WS(
            CHAR(10),
            NULLIF(
                TRIM(r.review_comment_title),
                ''
            ),
            TRIM(r.review_comment_message)
        )
    ) AS document_text,

    r.review_creation_date,
    r.review_answer_timestamp,

    CASE
        WHEN r.review_row_id =
             fulfillment.selected_review_row_id
        THEN 1
        ELSE 0
    END AS is_selected_order_review,

    fulfillment.review_row_count
        AS order_review_row_count,

    fulfillment.order_status,
    fulfillment.order_purchase_timestamp,

    fulfillment.customer_unique_id,
    fulfillment.customer_city,
    fulfillment.customer_state,

    fulfillment.is_delivery_metric_eligible,
    fulfillment.is_late_delivery,
    fulfillment.delivery_days,
    fulfillment.actual_minus_estimated_days,
    fulfillment.has_timeline_anomaly,

    fulfillment.item_record_count,
    fulfillment.distinct_product_count,
    fulfillment.distinct_seller_count,

    fulfillment.total_item_value,
    fulfillment.total_freight_value,
    fulfillment.total_order_value,
    fulfillment.total_payment_value

FROM raw_order_reviews AS r

INNER JOIN curated_order_fulfillment
    AS fulfillment
    ON fulfillment.order_id = r.order_id

WHERE r.review_comment_message IS NOT NULL
  AND TRIM(r.review_comment_message) <> '';

--执行迁移文件:
--Get-Content -Raw .\database\migrations\002_create_curated_views.sql | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"'
--创建完成后检查:
--"SHOW FULL TABLES WHERE Table_type = 'VIEW';" | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" -D "$MYSQL_DATABASE"'
