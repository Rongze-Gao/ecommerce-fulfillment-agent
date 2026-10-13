"""与 MCP 工具同口径的本地履约查询。

Agent 运行时通过 MCP SSE 调用 mcp_server 中的工具。
本模块不经过 MCP，直接查询 serving 表，便于核对 SQL 与指标定义。
改口径时需同时检查 curated 视图、本模块和 mcp_server。
"""

from datetime import date
from decimal import Decimal
import json
import re

from ecommerce_fulfillment_agent.infrastructure.mysql_client import (
    mysql_read_connection,
)


def _parse_iso_date(value: str, field_name: str) -> date:
    """Convert a YYYY-MM-DD string into a validated date."""

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format, "
            f"but received: {value!r}"
        ) from exc


def _normalize_customer_state(
    customer_state: str | None,
) -> str | None:
    """Validate and normalize an optional Brazilian state code."""

    if customer_state is None:
        return None

    normalized_state = customer_state.strip().upper()

    if not re.fullmatch(r"[A-Z]{2}", normalized_state):
        raise ValueError(
            "customer_state must be a two-letter code, "
            f"but received: {customer_state!r}"
        )

    return normalized_state


def _to_json_compatible(
    name: str,
    value: object,
) -> object:
    """Convert database values into JSON-compatible Python values."""

    if value is None:
        return None

    if name.endswith("_count"):
        return int(value)

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, date):
        return value.isoformat()

    return value


def get_fulfillment_overview(
    start_date: str,
    end_date: str,
    customer_state: str | None = None,
) -> dict[str, object]:
    """
    Return order and delivery KPIs for an inclusive date range.

    Business rules:
    - Both start_date and end_date are included.
    - Late delivery means the actual calendar delivery date is later
      than the estimated calendar delivery date.
    - The late-delivery denominator contains only orders eligible for
      the delivery metric.
    """

    parsed_start_date = _parse_iso_date(
        start_date,
        "start_date",
    )
    parsed_end_date = _parse_iso_date(
        end_date,
        "end_date",
    )

    if parsed_start_date > parsed_end_date:
        raise ValueError(
            "start_date must not be later than end_date."
        )

    normalized_state = _normalize_customer_state(
        customer_state
    )

    query = """
        SELECT
            COUNT(*) AS order_count,

            COALESCE(
                SUM(is_delivered),
                0
            ) AS delivered_order_count,

            ROUND(
                100.0 * SUM(is_delivered)
                / NULLIF(COUNT(*), 0),
                2
            ) AS delivered_order_rate_pct,

            COALESCE(
                SUM(is_canceled),
                0
            ) AS canceled_order_count,

            ROUND(
                100.0 * SUM(is_canceled)
                / NULLIF(COUNT(*), 0),
                2
            ) AS canceled_order_rate_pct,

            COALESCE(
                SUM(is_delivery_metric_eligible),
                0
            ) AS delivery_metric_eligible_order_count,

            COALESCE(
                SUM(
                    CASE
                        WHEN is_delivery_metric_eligible = 1
                         AND is_late_delivery = 1
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS late_delivery_count,

            ROUND(
                100.0
                * SUM(
                    CASE
                        WHEN is_delivery_metric_eligible = 1
                         AND is_late_delivery = 1
                        THEN 1
                        ELSE 0
                    END
                )
                / NULLIF(
                    SUM(is_delivery_metric_eligible),
                    0
                ),
                2
            ) AS late_delivery_rate_pct,

            ROUND(
                AVG(
                    CASE
                        WHEN is_delivery_metric_eligible = 1
                        THEN delivery_days
                    END
                ),
                2
            ) AS average_delivery_days,

            ROUND(
                AVG(
                    CASE
                        WHEN is_delivery_metric_eligible = 1
                        THEN actual_minus_estimated_days
                    END
                ),
                2
            ) AS average_actual_minus_estimated_days,

            COALESCE(
                SUM(has_timeline_anomaly),
                0
            ) AS timeline_anomaly_order_count

        FROM serving_order_fulfillment

        WHERE purchase_date >= %s
          AND purchase_date <= %s
    """

    parameters: list[object] = [
        parsed_start_date,
        parsed_end_date,
    ]

    if normalized_state is not None:
        query += """
          AND customer_state = %s
        """
        parameters.append(normalized_state)

    with mysql_read_connection() as connection:
        cursor = connection.cursor(
            dictionary=True,
            buffered=True,
        )

        try:
            cursor.execute(
                query,
                tuple(parameters),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()

    if row is None:
        raise RuntimeError(
            "Fulfillment overview query returned no result."
        )

    metrics = {
        name: _to_json_compatible(name, value)
        for name, value in row.items()
    }

    return {
        "filters": {
            "start_date": parsed_start_date.isoformat(),
            "end_date": parsed_end_date.isoformat(),
            "customer_state": normalized_state,
        },
        "business_rules": {
            "date_range": "inclusive",
            "late_delivery": (
                "actual delivery calendar date is later than "
                "estimated delivery calendar date"
            ),
            "late_rate_denominator": (
                "delivery_metric_eligible_order_count"
            ),
        },
        "metrics": metrics,
    }


if __name__ == "__main__":
    overview = get_fulfillment_overview(
        start_date="2018-01-01",
        end_date="2018-06-30",
    )

    print(
        json.dumps(
            overview,
            ensure_ascii=False,
            indent=2,
        )
    )