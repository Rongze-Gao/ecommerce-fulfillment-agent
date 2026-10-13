"""Validate the final serving-layer data contracts.

This module only reads data. It does not insert, update, delete,
clean, or rebuild any MySQL tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ecommerce_fulfillment_agent.infrastructure.mysql_client import (
    mysql_read_connection,
)


@dataclass(frozen=True)
class CountCheck:
    """Compare a serving-table row count with its expected source count."""

    name: str
    description: str
    sql: str


@dataclass(frozen=True)
class ViolationCheck:
    """A validation query that must return zero invalid rows."""

    name: str
    description: str
    sql: str


COUNT_CHECKS: tuple[CountCheck, ...] = (
    CountCheck(
        name="Order-level row completeness",
        description=(
            "Every raw order must produce exactly one "
            "serving_order_fulfillment row."
        ),
        sql="""
            SELECT
                (SELECT COUNT(*)
                 FROM serving_order_fulfillment) AS actual_count,
                (SELECT COUNT(*)
                 FROM raw_orders) AS expected_count
        """,
    ),
    CountCheck(
        name="Order-item row completeness",
        description=(
            "Every raw order item must produce exactly one "
            "serving_order_item_detail row."
        ),
        sql="""
            SELECT
                (SELECT COUNT(*)
                 FROM serving_order_item_detail) AS actual_count,
                (SELECT COUNT(*)
                 FROM raw_order_items) AS expected_count
        """,
    ),
    CountCheck(
        name="Review-corpus row completeness",
        description=(
            "Every raw review containing a non-empty message "
            "must produce one serving_review_corpus document."
        ),
        sql="""
            SELECT
                (SELECT COUNT(*)
                 FROM serving_review_corpus) AS actual_count,
                (
                    SELECT COUNT(*)
                    FROM raw_order_reviews
                    WHERE review_comment_message IS NOT NULL
                      AND TRIM(review_comment_message) <> ''
                ) AS expected_count
        """,
    ),
)


VIOLATION_CHECKS: tuple[ViolationCheck, ...] = (
    # ------------------------------------------------------------------
    # 1. Primary-key and document-identifier contracts
    # ------------------------------------------------------------------
    ViolationCheck(
        name="Order keys are present",
        description="order_id must not be NULL.",
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE order_id IS NULL
        """,
    ),
    ViolationCheck(
        name="Order keys are unique",
        description="One order_id must correspond to exactly one order row.",
        sql="""
            SELECT COUNT(*)
            FROM (
                SELECT order_id
                FROM serving_order_fulfillment
                GROUP BY order_id
                HAVING COUNT(*) > 1
            ) AS duplicated_orders
        """,
    ),
    ViolationCheck(
        name="Order-item keys are present",
        description=(
            "order_id and order_item_id must both contain values."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail
            WHERE order_id IS NULL
               OR order_item_id IS NULL
        """,
    ),
    ViolationCheck(
        name="Order-item keys are unique",
        description=(
            "The order_id and order_item_id pair must identify one item."
        ),
        sql="""
            SELECT COUNT(*)
            FROM (
                SELECT order_id, order_item_id
                FROM serving_order_item_detail
                GROUP BY order_id, order_item_id
                HAVING COUNT(*) > 1
            ) AS duplicated_order_items
        """,
    ),
    ViolationCheck(
        name="Review document identifiers are present",
        description="document_id and review_row_id must contain values.",
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus
            WHERE document_id IS NULL
               OR TRIM(document_id) = ''
               OR review_row_id IS NULL
        """,
    ),
    ViolationCheck(
        name="Review document identifiers are unique",
        description="Each document_id must identify one RAG document.",
        sql="""
            SELECT COUNT(*)
            FROM (
                SELECT document_id
                FROM serving_review_corpus
                GROUP BY document_id
                HAVING COUNT(*) > 1
            ) AS duplicated_documents
        """,
    ),
    ViolationCheck(
        name="Review row identifiers are unique",
        description="Each raw review row may enter the corpus only once.",
        sql="""
            SELECT COUNT(*)
            FROM (
                SELECT review_row_id
                FROM serving_review_corpus
                GROUP BY review_row_id
                HAVING COUNT(*) > 1
            ) AS duplicated_review_rows
        """,
    ),

    # ------------------------------------------------------------------
    # 2. Relationships between serving and raw tables
    # ------------------------------------------------------------------
    ViolationCheck(
        name="Order items reference existing orders",
        description=(
            "Every serving order item must reference an order-level row."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail AS item
            LEFT JOIN serving_order_fulfillment AS order_data
                ON order_data.order_id = item.order_id
            WHERE order_data.order_id IS NULL
        """,
    ),
    ViolationCheck(
        name="Review documents reference existing orders",
        description=(
            "Every review document must reference an order-level row."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus AS review_data
            LEFT JOIN serving_order_fulfillment AS order_data
                ON order_data.order_id = review_data.order_id
            WHERE order_data.order_id IS NULL
        """,
    ),
    ViolationCheck(
        name="Review documents reference raw reviews",
        description=(
            "Every corpus review_row_id must come from raw_order_reviews."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus AS review_data
            LEFT JOIN raw_order_reviews AS raw_review
                ON raw_review.review_row_id = review_data.review_row_id
            WHERE raw_review.review_row_id IS NULL
        """,
    ),

    # ------------------------------------------------------------------
    # 3. Order-level business-rule contracts
    # ------------------------------------------------------------------
    ViolationCheck(
        name="Order flags use valid value domains",
        description=(
            "Most order flags must be binary; is_late_delivery "
            "may also be NULL when delivery cannot be measured."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE is_delivered IS NULL
               OR is_delivered NOT IN (0, 1)
               OR is_canceled IS NULL
               OR is_canceled NOT IN (0, 1)
               OR has_delivery_date IS NULL
               OR has_delivery_date NOT IN (0, 1)
               OR is_delivery_metric_eligible IS NULL
               OR is_delivery_metric_eligible NOT IN (0, 1)
               OR (
                  is_late_delivery IS NOT NULL
                  AND is_late_delivery NOT IN (0, 1)
               )
               OR has_approval_before_purchase_anomaly IS NULL
               OR has_approval_before_purchase_anomaly NOT IN (0, 1)
               OR has_carrier_before_approval_anomaly IS NULL
               OR has_carrier_before_approval_anomaly NOT IN (0, 1)
               OR has_delivery_before_carrier_anomaly IS NULL
               OR has_delivery_before_carrier_anomaly NOT IN (0, 1)
               OR has_status_date_anomaly IS NULL
               OR has_status_date_anomaly NOT IN (0, 1)
               OR has_timeline_anomaly IS NULL
               OR has_timeline_anomaly NOT IN (0, 1)
               OR has_item_records IS NULL
               OR has_item_records NOT IN (0, 1)
               OR has_payment_records IS NULL
               OR has_payment_records NOT IN (0, 1)
               OR has_review IS NULL
               OR has_review NOT IN (0, 1)
               OR has_review_message IS NULL
               OR has_review_message NOT IN (0, 1)
        """,
    ),
    ViolationCheck(
        name="Eligible delivery metrics have required data",
        description=(
            "An eligible order must be delivered and have both actual "
            "and estimated delivery dates."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE is_delivery_metric_eligible = 1
              AND (
                    order_status <> 'delivered'
                    OR order_delivered_customer_date IS NULL
                    OR order_estimated_delivery_date IS NULL
              )
        """,
    ),
    ViolationCheck(
        name="Late-delivery availability matches eligibility",
        description=(
            "Eligible orders must have a zero-or-one late result; "
            "ineligible orders must keep the result as NULL."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE (
                    is_delivery_metric_eligible = 1
                    AND is_late_delivery IS NULL
                  )
               OR (
                    is_delivery_metric_eligible = 0
                    AND is_late_delivery IS NOT NULL
                  )
        """,
    ),
    ViolationCheck(
        name="Late-delivery rule is consistent",
        description=(
            "For eligible orders, the actual calendar delivery date "
            "must be compared with the estimated calendar date."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE is_delivery_metric_eligible = 1
              AND is_late_delivery <>
                  CASE
                      WHEN DATE(order_delivered_customer_date)
                           > DATE(order_estimated_delivery_date)
                      THEN 1
                      ELSE 0
                  END
        """,
    ),
    ViolationCheck(
        name="Timeline anomaly summary is consistent",
        description=(
            "has_timeline_anomaly must summarize the four detailed "
            "anomaly flags."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE has_timeline_anomaly <>
                  CASE
                      WHEN has_approval_before_purchase_anomaly = 1
                        OR has_carrier_before_approval_anomaly = 1
                        OR has_delivery_before_carrier_anomaly = 1
                        OR has_status_date_anomaly = 1
                      THEN 1
                      ELSE 0
                  END
        """,
    ),
    ViolationCheck(
        name="Record-presence flags match record counts",
        description=(
            "Item, payment, and review presence flags must agree "
            "with their corresponding record counts."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE has_item_records <>
                  CASE WHEN item_record_count > 0 THEN 1 ELSE 0 END
               OR has_payment_records <>
                  CASE WHEN payment_record_count > 0 THEN 1 ELSE 0 END
               OR has_review <>
                  CASE WHEN review_row_count > 0 THEN 1 ELSE 0 END
        """,
    ),
    ViolationCheck(
        name="Review-message flags match review text",
        description=(
            "has_review_message must describe whether the selected "
            "review has a non-empty comment."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE has_review_message <>
                  CASE
                      WHEN review_comment_message IS NOT NULL
                       AND TRIM(review_comment_message) <> ''
                      THEN 1
                      ELSE 0
                  END
        """,
    ),
    ViolationCheck(
        name="Order totals are mathematically consistent",
        description=(
            "total_order_value must equal item value plus freight value."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE has_item_records = 1
              AND ABS(
                    total_order_value
                    - total_item_value
                    - total_freight_value
                  ) > 0.01
        """,
    ),
    ViolationCheck(
        name="Order monetary values are non-negative",
        description=(
            "Item, freight, order, and payment totals cannot be negative."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_fulfillment
            WHERE total_item_value < 0
               OR total_freight_value < 0
               OR total_order_value < 0
               OR total_payment_value < 0
        """,
    ),

    # ------------------------------------------------------------------
    # 4. Order-item business-rule contracts
    # ------------------------------------------------------------------
    ViolationCheck(
        name="Order-item business identifiers are present",
        description="Each item must have a product and seller identifier.",
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail
            WHERE product_id IS NULL
               OR seller_id IS NULL
        """,
    ),
    ViolationCheck(
        name="Order-item flags use valid value domains",
        description=(
            "Eligibility and anomaly flags must be binary; "
            "late-delivery and shipping-limit results may be NULL "
            "when they cannot be measured."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail
            WHERE is_delivery_metric_eligible IS NULL
               OR is_delivery_metric_eligible NOT IN (0, 1)
               OR (
                    is_late_delivery IS NOT NULL
                    AND is_late_delivery NOT IN (0, 1)
                  )
               OR has_timeline_anomaly IS NULL
               OR has_timeline_anomaly NOT IN (0, 1)
               OR (
                    is_shipping_limit_missed IS NOT NULL
                    AND is_shipping_limit_missed NOT IN (0, 1)
                  )
        """,
    ),
    ViolationCheck(
        name="Item totals are mathematically consistent",
        description="item_total_value must equal price plus freight.",
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail
            WHERE ABS(item_total_value - price - freight_value) > 0.01
        """,
    ),
    ViolationCheck(
        name="Item monetary values are non-negative",
        description="Price, freight, and item totals cannot be negative.",
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail
            WHERE price < 0
               OR freight_value < 0
               OR item_total_value < 0
        """,
    ),
    ViolationCheck(
        name="Item order-level metrics match their parent order",
        description=(
            "Repeated order-level fields in the item table must agree "
            "with serving_order_fulfillment."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail AS item
            INNER JOIN serving_order_fulfillment AS order_data
                ON order_data.order_id = item.order_id
            WHERE NOT (
                    item.is_delivery_metric_eligible
                    <=> order_data.is_delivery_metric_eligible
                  )
               OR NOT (
                    item.is_late_delivery
                    <=> order_data.is_late_delivery
                  )
               OR NOT (
                    item.delivery_days
                    <=> order_data.delivery_days
                  )
               OR NOT (
                    item.actual_minus_estimated_days
                    <=> order_data.actual_minus_estimated_days
                  )
               OR NOT (
                    item.has_timeline_anomaly
                    <=> order_data.has_timeline_anomaly
                  )
        """,
    ),
    ViolationCheck(
        name="Shipping-limit result matches date availability",
        description=(
            "The shipping-limit result must be NULL when required "
            "timestamps are missing; otherwise it must use the "
            "calendar-date shipping-limit rule."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail
            WHERE (
                    (
                        order_delivered_carrier_date IS NULL
                        OR shipping_limit_date IS NULL
                    )
                    AND is_shipping_limit_missed IS NOT NULL
                  )
               OR (
                    order_delivered_carrier_date IS NOT NULL
                    AND shipping_limit_date IS NOT NULL
                    AND (
                        is_shipping_limit_missed IS NULL
                        OR is_shipping_limit_missed <>
                           CASE
                               WHEN DATE(order_delivered_carrier_date)
                                    > DATE(shipping_limit_date)
                               THEN 1
                               ELSE 0
                           END
                    )
                  )
        """,
    ),
    ViolationCheck(
        name="Item review scores use the valid scale",
        description="A non-NULL review score must be between 1 and 5.",
        sql="""
            SELECT COUNT(*)
            FROM serving_order_item_detail
            WHERE review_score IS NOT NULL
              AND review_score NOT BETWEEN 1 AND 5
        """,
    ),

    # ------------------------------------------------------------------
    # 5. RAG review-corpus contracts
    # ------------------------------------------------------------------
    ViolationCheck(
        name="Review corpus contains usable text",
        description=(
            "RAG documents must have both a source review message "
            "and generated document text."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus
            WHERE review_comment_message IS NULL
               OR TRIM(review_comment_message) = ''
               OR document_text IS NULL
               OR TRIM(document_text) = ''
        """,
    ),
    ViolationCheck(
        name="Review-corpus scores use the valid scale",
        description="Every corpus review score must be between 1 and 5.",
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus
            WHERE review_score IS NULL
               OR review_score NOT BETWEEN 1 AND 5
        """,
    ),
    ViolationCheck(
        name="Review-corpus flags use valid value domains",
        description=(
            "Most review-document flags must be binary; "
            "is_late_delivery may be NULL when it cannot be measured."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus
            WHERE is_selected_order_review IS NULL
               OR is_selected_order_review NOT IN (0, 1)
               OR is_delivery_metric_eligible IS NULL
               OR is_delivery_metric_eligible NOT IN (0, 1)
               OR (
                 is_late_delivery IS NOT NULL
                 AND is_late_delivery NOT IN (0, 1)
               )
               OR has_timeline_anomaly IS NULL
               OR has_timeline_anomaly NOT IN (0, 1)
        """,
    ),
    ViolationCheck(
        name="Review-corpus order counts are valid",
        description=(
            "A review document must belong to an order containing "
            "at least one review row."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus
            WHERE order_review_row_count IS NULL
               OR order_review_row_count < 1
        """,
    ),
    ViolationCheck(
        name="Review-corpus order metrics match parent orders",
        description=(
            "Order-level fields attached to each RAG document must "
            "agree with serving_order_fulfillment."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus AS review_data
            INNER JOIN serving_order_fulfillment AS order_data
                ON order_data.order_id = review_data.order_id
            WHERE NOT (
                    review_data.is_delivery_metric_eligible
                    <=> order_data.is_delivery_metric_eligible
                  )
               OR NOT (
                    review_data.is_late_delivery
                    <=> order_data.is_late_delivery
                  )
               OR NOT (
                    review_data.delivery_days
                    <=> order_data.delivery_days
                  )
               OR NOT (
                    review_data.actual_minus_estimated_days
                    <=> order_data.actual_minus_estimated_days
                  )
               OR NOT (
                    review_data.has_timeline_anomaly
                    <=> order_data.has_timeline_anomaly
                  )
        """,
    ),
    ViolationCheck(
        name="Review-corpus monetary values are non-negative",
        description=(
            "Order values attached to review documents cannot be negative."
        ),
        sql="""
            SELECT COUNT(*)
            FROM serving_review_corpus
            WHERE total_item_value < 0
               OR total_freight_value < 0
               OR total_order_value < 0
               OR total_payment_value < 0
        """,
    ),
)


def format_number(value: object) -> str:
    """Format numeric output with thousands separators."""

    if isinstance(value, (int, Decimal)):
        return f"{value:,}"

    return str(value)


def run_count_checks(cursor) -> int:
    """Run serving-to-source row-count comparisons."""

    failed_count = 0

    print("\n=== 1. Serving-table completeness ===")
    print("Compare serving row counts with their expected raw-data sources.")

    for check in COUNT_CHECKS:
        cursor.execute(check.sql)
        actual_count, expected_count = cursor.fetchone()

        passed = actual_count == expected_count
        status = "PASS" if passed else "FAIL"

        print(
            f"[{status}] {check.name}: "
            f"actual={format_number(actual_count)}, "
            f"expected={format_number(expected_count)}"
        )

        if not passed:
            print(f"       Rule: {check.description}")
            failed_count += 1

    return failed_count


def run_violation_checks(cursor) -> int:
    """Run checks in which the expected violation count is zero."""

    failed_count = 0

    print("\n=== 2. Data-contract violations ===")
    print("Every check below must return zero invalid rows.")

    for check in VIOLATION_CHECKS:
        cursor.execute(check.sql)
        violation_count = cursor.fetchone()[0]

        passed = violation_count == 0
        status = "PASS" if passed else "FAIL"

        print(
            f"[{status}] {check.name}: "
            f"violations={format_number(violation_count)}"
        )

        if not passed:
            print(f"       Rule: {check.description}")
            failed_count += 1

    return failed_count


def print_information_metrics(cursor) -> None:
    """Display preserved business signals that are not validation failures."""

    cursor.execute(
        """
        SELECT
            COUNT(*) AS order_count,
            COALESCE(SUM(is_late_delivery), 0) AS late_order_count,
            COALESCE(SUM(has_timeline_anomaly), 0) AS anomaly_order_count,
            COALESCE(SUM(is_canceled), 0) AS canceled_order_count
        FROM serving_order_fulfillment
        """
    )

    (
        order_count,
        late_order_count,
        anomaly_order_count,
        canceled_order_count,
    ) = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(is_shipping_limit_missed), 0)
        FROM serving_order_item_detail
        """
    )
    missed_shipping_item_count = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM serving_review_corpus
        """
    )
    review_document_count = cursor.fetchone()[0]

    print("\n=== 3. Preserved business signals ===")
    print("These values are evidence retained for later Agent investigation.")
    print(f"Orders: {format_number(order_count)}")
    print(f"Late-delivery orders: {format_number(late_order_count)}")
    print(f"Timeline-anomaly orders: {format_number(anomaly_order_count)}")
    print(f"Canceled orders: {format_number(canceled_order_count)}")
    print(
        "Order items with missed shipping limits: "        f"{format_number(missed_shipping_item_count)}"
    )
    print(f"Review documents for RAG: {format_number(review_document_count)}")


def main() -> int:
    """Run all final serving-layer validation checks."""

    print("Mode: read-only serving-layer validation")
    print(
        "Tables: serving_order_fulfillment, "
        "serving_order_item_detail, serving_review_corpus"
    )

    try:
        with mysql_read_connection() as connection:
            cursor = connection.cursor()

            try:
                failed_count = 0
                failed_count += run_count_checks(cursor)
                failed_count += run_violation_checks(cursor)
                print_information_metrics(cursor)
            finally:
                cursor.close()

    except Exception as error:
        print(f"\n[FAIL] Serving-data validation could not complete: {error}")
        return 1

    if failed_count > 0:
        print(
            f"\n[FAIL] Serving-data validation found "
            f"{failed_count} failed contract check(s)."
        )
        return 1

    print(
        "\n[PASS] All serving-layer data contracts "
        "were validated successfully."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())