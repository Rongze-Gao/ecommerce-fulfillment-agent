from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class ProfileQuery:
    """描述一项只读数据画像查询。"""

    title: str
    purpose: str
    sql: str


PROFILE_QUERIES = (
    ProfileQuery(
        title="1. Raw table row counts",
        purpose="检查9张原始表当前保存的记录数量。",
        sql="""
            SELECT 'raw_category_translation' AS table_name,
                   COUNT(*) AS row_count
            FROM raw_category_translation

            UNION ALL

            SELECT 'raw_customers', COUNT(*)
            FROM raw_customers

            UNION ALL

            SELECT 'raw_geolocation', COUNT(*)
            FROM raw_geolocation

            UNION ALL

            SELECT 'raw_order_items', COUNT(*)
            FROM raw_order_items

            UNION ALL

            SELECT 'raw_order_payments', COUNT(*)
            FROM raw_order_payments

            UNION ALL

            SELECT 'raw_order_reviews', COUNT(*)
            FROM raw_order_reviews

            UNION ALL

            SELECT 'raw_orders', COUNT(*)
            FROM raw_orders

            UNION ALL

            SELECT 'raw_products', COUNT(*)
            FROM raw_products

            UNION ALL

            SELECT 'raw_sellers', COUNT(*)
            FROM raw_sellers

            ORDER BY table_name
        """,
    ),
    ProfileQuery(
        title="2. Order status distribution",
        purpose="查看真实订单状态及各状态占比。",
        sql="""
            SELECT
                order_status,
                COUNT(*) AS order_count,
                ROUND(
                    100.0 * COUNT(*)
                    / (SELECT COUNT(*) FROM raw_orders),
                    2
                ) AS percentage
            FROM raw_orders
            GROUP BY order_status
            ORDER BY order_count DESC, order_status
        """,
    ),
    ProfileQuery(
        title="3. Order time coverage",
        purpose="确定数据实际覆盖的历史时间范围。",
        sql="""
            SELECT
                MIN(order_purchase_timestamp) AS first_purchase,
                MAX(order_purchase_timestamp) AS last_purchase,
                COUNT(
                    DISTINCT DATE_FORMAT(
                        order_purchase_timestamp,
                        '%Y-%m'
                    )
                ) AS purchase_months
            FROM raw_orders
        """,
    ),
    ProfileQuery(
        title="4. Missing values",
        purpose="检查后续业务分析会使用的重要字段缺失情况。",
        sql="""
            SELECT
                'raw_orders.order_approved_at' AS field_name,
                COUNT(*) AS total_rows,
                SUM(order_approved_at IS NULL) AS missing_rows,
                ROUND(
                    100.0 * SUM(order_approved_at IS NULL)
                    / COUNT(*),
                    2
                ) AS missing_percentage
            FROM raw_orders

            UNION ALL

            SELECT
                'raw_orders.order_delivered_carrier_date',
                COUNT(*),
                SUM(order_delivered_carrier_date IS NULL),
                ROUND(
                    100.0
                    * SUM(order_delivered_carrier_date IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_orders

            UNION ALL

            SELECT
                'raw_orders.order_delivered_customer_date',
                COUNT(*),
                SUM(order_delivered_customer_date IS NULL),
                ROUND(
                    100.0
                    * SUM(order_delivered_customer_date IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_orders

            UNION ALL

            SELECT
                'raw_order_reviews.review_comment_title',
                COUNT(*),
                SUM(
                    review_comment_title IS NULL
                    OR TRIM(review_comment_title) = ''
                ),
                ROUND(
                    100.0 * SUM(
                        review_comment_title IS NULL
                        OR TRIM(review_comment_title) = ''
                    ) / COUNT(*),
                    2
                )
            FROM raw_order_reviews

            UNION ALL

            SELECT
                'raw_order_reviews.review_comment_message',
                COUNT(*),
                SUM(
                    review_comment_message IS NULL
                    OR TRIM(review_comment_message) = ''
                ),
                ROUND(
                    100.0 * SUM(
                        review_comment_message IS NULL
                        OR TRIM(review_comment_message) = ''
                    ) / COUNT(*),
                    2
                )
            FROM raw_order_reviews

            UNION ALL

            SELECT
                'raw_products.product_category_name',
                COUNT(*),
                SUM(product_category_name IS NULL),
                ROUND(
                    100.0
                    * SUM(product_category_name IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_products

            UNION ALL

            SELECT
                'raw_products.product_name_lenght',
                COUNT(*),
                SUM(product_name_lenght IS NULL),
                ROUND(
                    100.0
                    * SUM(product_name_lenght IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_products

            UNION ALL

            SELECT
                'raw_products.product_description_lenght',
                COUNT(*),
                SUM(product_description_lenght IS NULL),
                ROUND(
                    100.0
                    * SUM(product_description_lenght IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_products

            UNION ALL

            SELECT
                'raw_products.product_photos_qty',
                COUNT(*),
                SUM(product_photos_qty IS NULL),
                ROUND(
                    100.0
                    * SUM(product_photos_qty IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_products

            UNION ALL

            SELECT
                'raw_products.product_weight_g',
                COUNT(*),
                SUM(product_weight_g IS NULL),
                ROUND(
                    100.0
                    * SUM(product_weight_g IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_products

            UNION ALL

            SELECT
                'raw_products.product_length_cm',
                COUNT(*),
                SUM(product_length_cm IS NULL),
                ROUND(
                    100.0
                    * SUM(product_length_cm IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_products

            UNION ALL

            SELECT
                'raw_products.product_height_cm',
                COUNT(*),
                SUM(product_height_cm IS NULL),
                ROUND(
                    100.0
                    * SUM(product_height_cm IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_products

            UNION ALL

            SELECT
                'raw_products.product_width_cm',
                COUNT(*),
                SUM(product_width_cm IS NULL),
                ROUND(
                    100.0
                    * SUM(product_width_cm IS NULL)
                    / COUNT(*),
                    2
                )
            FROM raw_products

            ORDER BY field_name
        """,
    ),
    ProfileQuery(
        title="5. Relationship integrity",
        purpose="检查各表之间是否存在无法关联的记录。",
        sql="""
            SELECT
                'orders.customer_id -> customers.customer_id'
                    AS relationship_name,
                COUNT(*) AS unmatched_rows
            FROM raw_orders AS o
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw_customers AS c
                WHERE c.customer_id = o.customer_id
            )

            UNION ALL

            SELECT
                'order_items.order_id -> orders.order_id',
                COUNT(*)
            FROM raw_order_items AS oi
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw_orders AS o
                WHERE o.order_id = oi.order_id
            )

            UNION ALL

            SELECT
                'order_items.product_id -> products.product_id',
                COUNT(*)
            FROM raw_order_items AS oi
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw_products AS p
                WHERE p.product_id = oi.product_id
            )

            UNION ALL

            SELECT
                'order_items.seller_id -> sellers.seller_id',
                COUNT(*)
            FROM raw_order_items AS oi
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw_sellers AS s
                WHERE s.seller_id = oi.seller_id
            )

            UNION ALL

            SELECT
                'payments.order_id -> orders.order_id',
                COUNT(*)
            FROM raw_order_payments AS op
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw_orders AS o
                WHERE o.order_id = op.order_id
            )

            UNION ALL

            SELECT
                'reviews.order_id -> orders.order_id',
                COUNT(*)
            FROM raw_order_reviews AS r
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw_orders AS o
                WHERE o.order_id = r.order_id
            )

            UNION ALL

            SELECT
                'products.category -> category_translation',
                COUNT(*)
            FROM raw_products AS p
            WHERE p.product_category_name IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM raw_category_translation AS t
                  WHERE t.product_category_name =
                        p.product_category_name
              )

            UNION ALL

            SELECT
                'customers.zip_prefix -> geolocation.zip_prefix',
                COUNT(*)
            FROM raw_customers AS c
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw_geolocation AS g
                WHERE g.geolocation_zip_code_prefix =
                      c.customer_zip_code_prefix
            )

            UNION ALL

            SELECT
                'sellers.zip_prefix -> geolocation.zip_prefix',
                COUNT(*)
            FROM raw_sellers AS s
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw_geolocation AS g
                WHERE g.geolocation_zip_code_prefix =
                      s.seller_zip_code_prefix
            )

            ORDER BY relationship_name
        """,
    ),
    ProfileQuery(
        title="6. One-to-many relationship profile",
        purpose="识别直接连接表时可能造成重复计算的一对多关系。",
        sql="""
            SELECT
                'distinct_customer_unique_ids' AS metric_name,
                COUNT(DISTINCT customer_unique_id) AS metric_value
            FROM raw_customers

            UNION ALL

            SELECT
                'customer_unique_ids_with_multiple_customer_ids',
                COUNT(*)
            FROM (
                SELECT customer_unique_id
                FROM raw_customers
                GROUP BY customer_unique_id
                HAVING COUNT(*) > 1
            ) AS repeated_customers

            UNION ALL

            SELECT
                'maximum_customer_ids_for_one_unique_customer',
                MAX(customer_id_count)
            FROM (
                SELECT
                    customer_unique_id,
                    COUNT(*) AS customer_id_count
                FROM raw_customers
                GROUP BY customer_unique_id
            ) AS customer_counts

            UNION ALL

            SELECT
                'orders_with_multiple_items',
                COUNT(*)
            FROM (
                SELECT order_id
                FROM raw_order_items
                GROUP BY order_id
                HAVING COUNT(*) > 1
            ) AS multi_item_orders

            UNION ALL

            SELECT
                'maximum_items_in_one_order',
                MAX(item_count)
            FROM (
                SELECT
                    order_id,
                    COUNT(*) AS item_count
                FROM raw_order_items
                GROUP BY order_id
            ) AS item_counts

            UNION ALL

            SELECT
                'orders_with_multiple_payment_records',
                COUNT(*)
            FROM (
                SELECT order_id
                FROM raw_order_payments
                GROUP BY order_id
                HAVING COUNT(*) > 1
            ) AS multi_payment_orders

            UNION ALL

            SELECT
                'maximum_payment_records_for_one_order',
                MAX(payment_count)
            FROM (
                SELECT
                    order_id,
                    COUNT(*) AS payment_count
                FROM raw_order_payments
                GROUP BY order_id
            ) AS payment_counts

            UNION ALL

            SELECT
                'orders_with_multiple_review_rows',
                COUNT(*)
            FROM (
                SELECT order_id
                FROM raw_order_reviews
                GROUP BY order_id
                HAVING COUNT(*) > 1
            ) AS multi_review_orders

            UNION ALL

            SELECT
                'duplicated_review_ids',
                COUNT(*)
            FROM (
                SELECT review_id
                FROM raw_order_reviews
                GROUP BY review_id
                HAVING COUNT(*) > 1
            ) AS repeated_review_ids

            ORDER BY metric_name
        """,
    ),
    ProfileQuery(
        title="7. Order timestamp consistency",
        purpose="检查订单事件时间是否存在不可能或可疑的先后顺序。",
        sql="""
            SELECT
                'approved_before_purchase' AS check_name,
                COUNT(*) AS affected_orders
            FROM raw_orders
            WHERE order_approved_at IS NOT NULL
              AND order_approved_at < order_purchase_timestamp

            UNION ALL

            SELECT
                'carrier_received_before_approval',
                COUNT(*)
            FROM raw_orders
            WHERE order_delivered_carrier_date IS NOT NULL
              AND order_approved_at IS NOT NULL
              AND order_delivered_carrier_date <
                  order_approved_at

            UNION ALL

            SELECT
                'customer_delivery_before_carrier_received',
                COUNT(*)
            FROM raw_orders
            WHERE order_delivered_customer_date IS NOT NULL
              AND order_delivered_carrier_date IS NOT NULL
              AND order_delivered_customer_date <
                  order_delivered_carrier_date

            UNION ALL

            SELECT
                'estimated_delivery_before_purchase',
                COUNT(*)
            FROM raw_orders
            WHERE order_estimated_delivery_date <
                  order_purchase_timestamp

            UNION ALL

            SELECT
                'delivered_status_missing_customer_date',
                COUNT(*)
            FROM raw_orders
            WHERE order_status = 'delivered'
              AND order_delivered_customer_date IS NULL

            UNION ALL

            SELECT
                'non_delivered_status_with_customer_date',
                COUNT(*)
            FROM raw_orders
            WHERE order_status <> 'delivered'
              AND order_delivered_customer_date IS NOT NULL

            ORDER BY check_name
        """,
    ),
    ProfileQuery(
        title="8. Delivered-order performance",
        purpose="比较两种迟到判断规则，并统计实际配送表现。",
        sql="""
            SELECT
                COUNT(*) AS delivered_orders_with_dates,

                SUM(
                    order_delivered_customer_date >
                    order_estimated_delivery_date
                ) AS late_by_timestamp,

                SUM(
                    DATE(order_delivered_customer_date) >
                    DATE(order_estimated_delivery_date)
                ) AS late_by_calendar_date,

                ROUND(
                    100.0 * SUM(
                        DATE(order_delivered_customer_date) >
                        DATE(order_estimated_delivery_date)
                    ) / COUNT(*),
                    2
                ) AS late_by_calendar_date_percentage,

                ROUND(
                    AVG(
                        TIMESTAMPDIFF(
                            MINUTE,
                            order_purchase_timestamp,
                            order_delivered_customer_date
                        )
                    ) / 1440.0,
                    2
                ) AS average_delivery_days,

                ROUND(
                    AVG(
                        DATEDIFF(
                            DATE(order_delivered_customer_date),
                            DATE(order_estimated_delivery_date)
                        )
                    ),
                    2
                ) AS average_actual_minus_estimated_days

            FROM raw_orders
            WHERE order_status = 'delivered'
              AND order_delivered_customer_date IS NOT NULL
              AND order_estimated_delivery_date IS NOT NULL
        """,
    ),
    ProfileQuery(
        title="9. Geolocation ZIP-code multiplicity",
        purpose="判断为什么邮编地理数据必须先聚合再与订单关联。",
        sql="""
            WITH zip_counts AS (
                SELECT
                    geolocation_zip_code_prefix,
                    COUNT(*) AS records_per_zip
                FROM raw_geolocation
                GROUP BY geolocation_zip_code_prefix
            )
            SELECT
                SUM(records_per_zip) AS geolocation_rows,
                COUNT(*) AS distinct_zip_prefixes,
                SUM(records_per_zip) - COUNT(*) AS repeated_rows,
                SUM(records_per_zip > 1)
                    AS zip_prefixes_with_multiple_rows,
                MAX(records_per_zip)
                    AS maximum_rows_for_one_zip
            FROM zip_counts
        """,
    ),
    ProfileQuery(
        title="10. Coordinate range validation",
        purpose="检查经纬度是否处在合法范围内。",
        sql="""
            SELECT
                MIN(geolocation_lat) AS minimum_latitude,
                MAX(geolocation_lat) AS maximum_latitude,
                MIN(geolocation_lng) AS minimum_longitude,
                MAX(geolocation_lng) AS maximum_longitude,

                SUM(
                    geolocation_lat NOT BETWEEN -90 AND 90
                ) AS invalid_latitude_rows,

                SUM(
                    geolocation_lng NOT BETWEEN -180 AND 180
                ) AS invalid_longitude_rows

            FROM raw_geolocation
        """,
    ),
    ProfileQuery(
        title="11. Review text availability by score",
        purpose="统计未来RAG能够使用的真实评论文本数量。",
        sql="""
            SELECT
                review_score,
                COUNT(*) AS review_rows,

                SUM(
                    review_comment_message IS NOT NULL
                    AND TRIM(review_comment_message) <> ''
                ) AS reviews_with_message,

                ROUND(
                    100.0 * SUM(
                        review_comment_message IS NOT NULL
                        AND TRIM(review_comment_message) <> ''
                    ) / COUNT(*),
                    2
                ) AS message_percentage

            FROM raw_order_reviews
            GROUP BY review_score
            ORDER BY review_score
        """,
    ),
)


def require_environment_variable(name: str) -> str:
    """读取必需环境变量。"""

    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Required environment variable is missing: {name}"
        )

    return value


def format_cell(value: object) -> str:
    """将MySQL返回值转换为适合控制台显示的文本。"""

    if value is None:
        return "NULL"

    if isinstance(value, bool):
        return str(value)

    if isinstance(value, int):
        return f"{value:,}"

    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, datetime):
        return value.isoformat(sep=" ")

    if isinstance(value, date):
        return value.isoformat()

    return str(value)


def print_result_table(
    headers: tuple[str, ...],
    rows: list[tuple[object, ...]],
) -> None:
    """不依赖额外第三方包，在控制台打印对齐表格。"""

    if not rows:
        print("(no rows returned)")
        return

    formatted_rows = [
        [format_cell(value) for value in row]
        for row in rows
    ]

    widths = []

    for column_index, header in enumerate(headers):
        widths.append(
            max(
                len(header),
                max(
                    len(row[column_index])
                    for row in formatted_rows
                ),
            )
        )

    header_line = " | ".join(
        header.ljust(widths[index])
        for index, header in enumerate(headers)
    )

    separator_line = "-+-".join(
        "-" * width
        for width in widths
    )

    print(header_line)
    print(separator_line)

    for row in formatted_rows:
        print(
            " | ".join(
                value.ljust(widths[index])
                for index, value in enumerate(row)
            )
        )


def run_profile_query(
    cursor,
    query: ProfileQuery,
) -> None:
    """执行一项画像查询并显示结果。"""

    print(f"\n=== {query.title} ===")
    print(query.purpose)

    cursor.execute(query.sql)

    rows = cursor.fetchall()
    headers = tuple(cursor.column_names)

    print_result_table(
        headers,
        rows,
    )


def main() -> int:
    """连接MySQL并执行全部只读画像查询。"""

    if not ENV_FILE.is_file():
        print(
            f"[FAIL] Environment file not found: {ENV_FILE}"
        )
        return 1

    load_dotenv(
        dotenv_path=ENV_FILE,
        encoding="utf-8",
        override=False,
    )

    connection = None
    cursor = None

    try:
        host = require_environment_variable(
            "MYSQL_HOST"
        )

        port = int(
            require_environment_variable(
                "MYSQL_HOST_PORT"
            )
        )

        database = require_environment_variable(
            "MYSQL_DATABASE"
        )

        user = require_environment_variable(
            "MYSQL_USER"
        )

        password = require_environment_variable(
            "MYSQL_PASSWORD"
        )

        print(
            f"MySQL source: "
            f"{user}@{host}:{port}/{database}"
        )
        print("Mode: read-only raw-data profiling")

        connection = mysql.connector.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            charset="utf8mb4",
            use_unicode=True,
            autocommit=False,
            connection_timeout=10,
            raise_on_warnings=True,
        )

        # 数据库层面开启只读事务。
        # 即使程序中误写了INSERT/UPDATE，也会被MySQL拒绝。
        connection.start_transaction(
            readonly=True
        )

        cursor = connection.cursor()

        for query in PROFILE_QUERIES:
            run_profile_query(
                cursor,
                query,
            )

        # 画像过程没有需要提交的修改。
        connection.rollback()

        print(
            "\n[PASS] Raw-data profiling completed "
            "without modifying data."
        )

        return 0

    except (
        Error,
        OSError,
        ValueError,
        RuntimeError,
    ) as exc:
        if connection is not None:
            connection.rollback()

        print(
            f"\n[FAIL] Raw-data profiling failed: {exc}"
        )

        return 1

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())