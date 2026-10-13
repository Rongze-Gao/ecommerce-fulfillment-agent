"""通过 SSE 提供电商履约分析 MCP 服务。"""

from datetime import date
import re
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from ecommerce_fulfillment_agent.config import get_mcp_server_settings
from ecommerce_fulfillment_agent.infrastructure.mysql_client import (
    mysql_read_connection,
)


MCP_SETTINGS = get_mcp_server_settings()

DATASET_START_DATE = "2016-09-04"
DATASET_END_DATE = "2018-10-17"

VALID_CUSTOMER_STATES = frozenset(
    {
        "AC",
        "AL",
        "AM",
        "AP",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MG",
        "MS",
        "MT",
        "PA",
        "PB",
        "PE",
        "PI",
        "PR",
        "RJ",
        "RN",
        "RO",
        "RR",
        "RS",
        "SC",
        "SE",
        "SP",
        "TO",
    }
)

SegmentDimension = Literal[
    "purchase_month",
    "customer_state",
    "product_category",
    "seller",
]

RankMetric = Literal[
    "late_delivery_rate_pct",
    "canceled_order_rate_pct",
    "timeline_anomaly_rate_pct",
    "average_delivery_days",
    "shipping_limit_missed_rate_pct",
    "order_count",
]


ORDER_SEGMENT_COLUMNS = {
    "purchase_month": "purchase_month",
    "customer_state": "customer_state",
}

ITEM_SEGMENT_EXPRESSIONS = {
    "product_category": (
        "COALESCE("
        "NULLIF(TRIM(product_category_name), ''), "
        "'unknown'"
        ")"
    ),
    "seller": "seller_id",
}

SERVER_INSTRUCTIONS = """
这是一个只读的电商履约分析 MCP 服务。

服务基于 Olist 历史电商数据，为 AI Agent 提供确定性的履约指标查询和分组分析能力。
服务不会修改订单、顾客、商品、评论或其他业务数据，也不会执行退款、取消订单等操作。

当前项目能够分析迟到、取消、订单时间线异常，以及州、商品类别和卖家等维度的履约表现。
项目数据不包含退款、库存、仓库、承运商责任、天气或其他不可抗力信息。
""".strip()


mcp_server = FastMCP(
    name="ecommerce-fulfillment-analysis",
    instructions=SERVER_INSTRUCTIONS,
    host=MCP_SETTINGS.host,
    port=MCP_SETTINGS.port,
    sse_path="/sse",
    message_path="/messages/",
    log_level="INFO",
)


def _parse_iso_date(value: str, field_name: str) -> date:
    """将严格的 YYYY-MM-DD 字符串转换为 date。"""

    normalized_value = value.strip()

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_value) is None:
        raise ValueError(
            f"{field_name}必须使用YYYY-MM-DD格式，"
            f"当前值为：{value!r}"
        )

    try:
        return date.fromisoformat(normalized_value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name}不是有效日期，当前值为：{value!r}"
        ) from exc


def _normalize_customer_state(
    customer_state: str | None,
) -> str | None:
    """规范并验证巴西州代码。"""

    if customer_state is None or not customer_state.strip():
        return None

    normalized_state = customer_state.strip().upper()

    if normalized_state not in VALID_CUSTOMER_STATES:
        raise ValueError(
            "customer_state必须是有效的两位巴西州代码，"
            f"例如SP、RJ或MG；当前值为：{customer_state!r}"
        )

    return normalized_state


def _calculate_percentage(
    numerator: int,
    denominator: int,
) -> float | None:
    """计算百分比；分母为零时返回None。"""

    if denominator == 0:
        return None

    return round(numerator / denominator * 100, 2)


def _optional_float(value: object) -> float | None:
    """将MySQL数值转换为可JSON序列化的float。"""

    if value is None:
        return None

    return round(float(value), 2)


def _optional_iso_date(value: object) -> str | None:
    """将MySQL日期转换为ISO字符串。"""

    if value is None:
        return None

    if isinstance(value, date):
        return value.isoformat()

    return str(value)

def _query_segment_rows(
    dimension: SegmentDimension,
    start_date: date,
    end_date: date,
    customer_state: str | None,
) -> list[dict[str, Any]]:
    """根据分组维度执行只读聚合查询。"""

    conditions = [
        "purchase_date >= %s",
        "purchase_date <= %s",
    ]
    parameters: list[object] = [
        start_date,
        end_date,
    ]

    if customer_state is not None:
        conditions.append("customer_state = %s")
        parameters.append(customer_state)

    where_clause = " AND ".join(conditions)

    if dimension in ORDER_SEGMENT_COLUMNS:
        dimension_expression = ORDER_SEGMENT_COLUMNS[dimension]

        query = f"""
            SELECT
                {dimension_expression} AS segment_value,
                COUNT(*) AS order_count,

                COALESCE(
                    SUM(
                        CASE
                            WHEN order_status = 'canceled' THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS canceled_order_count,

                COALESCE(
                    SUM(
                        CASE
                            WHEN is_delivery_metric_eligible = 1
                                THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS delivery_metric_eligible_order_count,

                COALESCE(
                    SUM(
                        CASE
                            WHEN is_late_delivery = 1 THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS late_delivery_count,

                COALESCE(
                    SUM(
                        CASE
                            WHEN has_timeline_anomaly = 1 THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS timeline_anomaly_order_count,

                ROUND(
                    AVG(
                        CASE
                            WHEN is_delivery_metric_eligible = 1
                                THEN delivery_days
                            ELSE NULL
                        END
                    ),
                    2
                ) AS average_delivery_days,

                NULL AS shipping_limit_eligible_order_count,
                NULL AS shipping_limit_missed_order_count

            FROM serving_order_fulfillment

            WHERE {where_clause}

            GROUP BY {dimension_expression}
            ORDER BY segment_value
        """

    elif dimension in ITEM_SEGMENT_EXPRESSIONS:
        dimension_expression = ITEM_SEGMENT_EXPRESSIONS[dimension]

        query = f"""
            SELECT
                segment_value,
                COUNT(*) AS order_count,

                COALESCE(
                    SUM(is_canceled),
                    0
                ) AS canceled_order_count,

                COALESCE(
                    SUM(is_delivery_metric_eligible),
                    0
                ) AS delivery_metric_eligible_order_count,

                COALESCE(
                    SUM(
                        CASE
                            WHEN is_late_delivery = 1 THEN 1
                            ELSE 0
                        END
                    ),
                    0
                ) AS late_delivery_count,

                COALESCE(
                    SUM(has_timeline_anomaly),
                    0
                ) AS timeline_anomaly_order_count,

                ROUND(
                    AVG(
                        CASE
                            WHEN is_delivery_metric_eligible = 1
                                THEN delivery_days
                            ELSE NULL
                        END
                    ),
                    2
                ) AS average_delivery_days,

                COALESCE(
                    SUM(is_shipping_limit_eligible),
                    0
                ) AS shipping_limit_eligible_order_count,

                COALESCE(
                    SUM(is_shipping_limit_missed),
                    0
                ) AS shipping_limit_missed_order_count

            FROM (
                SELECT
                    {dimension_expression} AS segment_value,
                    order_id,

                    MAX(
                        CASE
                            WHEN order_status = 'canceled' THEN 1
                            ELSE 0
                        END
                    ) AS is_canceled,

                    MAX(
                        is_delivery_metric_eligible
                    ) AS is_delivery_metric_eligible,

                    MAX(
                        is_late_delivery
                    ) AS is_late_delivery,

                    MAX(
                        delivery_days
                    ) AS delivery_days,

                    MAX(
                        has_timeline_anomaly
                    ) AS has_timeline_anomaly,

                    MAX(
                        CASE
                            WHEN is_shipping_limit_missed IS NOT NULL
                                THEN 1
                            ELSE 0
                        END
                    ) AS is_shipping_limit_eligible,

                    MAX(
                        CASE
                            WHEN is_shipping_limit_missed = 1
                                THEN 1
                            ELSE 0
                        END
                    ) AS is_shipping_limit_missed

                FROM serving_order_item_detail

                WHERE {where_clause}

                GROUP BY
                    {dimension_expression},
                    order_id
            ) AS order_segments

            GROUP BY segment_value
            ORDER BY segment_value
        """

    else:
        raise ValueError(
            f"不支持的分组维度：{dimension}"
        )

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
            rows = cursor.fetchall()
        finally:
            cursor.close()

    return list(rows)

@mcp_server.tool()
def get_fulfillment_overview(
    start_date: str = DATASET_START_DATE,
    end_date: str = DATASET_END_DATE,
    customer_state: str | None = None,
) -> dict[str, Any]:
    """
    查询指定日期范围和可选顾客州的订单履约概览。

    当用户询问订单量、妥投率、取消率、迟到率、平均配送天数，
    或订单时间线异常数量时使用本工具。

    Args:
        start_date:
            订单购买日期范围的开始日期，格式为YYYY-MM-DD。
            默认值是Olist数据集的最早购买日期2016-09-04。
        end_date:
            订单购买日期范围的结束日期，格式为YYYY-MM-DD。
            默认值是Olist数据集的最晚购买日期2018-10-17。
        customer_state:
            可选的两位巴西顾客州代码，例如SP、RJ或MG。
            不传时统计全部州。

    Returns:
        包含实际筛选条件、业务口径、履约指标、数据来源和限制说明的字典。
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
            "start_date不能晚于end_date。"
        )

    normalized_state = _normalize_customer_state(
        customer_state
    )

    conditions = [
        "purchase_date >= %s",
        "purchase_date <= %s",
    ]
    parameters: list[object] = [
        parsed_start_date,
        parsed_end_date,
    ]

    if normalized_state is not None:
        conditions.append("customer_state = %s")
        parameters.append(normalized_state)

    query = f"""
        SELECT
            COUNT(*) AS order_count,

            COALESCE(
                SUM(
                    CASE
                        WHEN is_delivered = 1 THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS delivered_order_count,

            COALESCE(
                SUM(
                    CASE
                        WHEN is_canceled = 1 THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS canceled_order_count,

            COALESCE(
                SUM(
                    CASE
                        WHEN is_delivery_metric_eligible = 1 THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS delivery_metric_eligible_order_count,

            COALESCE(
                SUM(
                    CASE
                        WHEN is_late_delivery = 1 THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS late_delivery_count,

            ROUND(
                AVG(
                    CASE
                        WHEN is_delivery_metric_eligible = 1
                            THEN delivery_days
                        ELSE NULL
                    END
                ),
                2
            ) AS average_delivery_days,

            ROUND(
                AVG(
                    CASE
                        WHEN is_delivery_metric_eligible = 1
                            THEN actual_minus_estimated_days
                        ELSE NULL
                    END
                ),
                2
            ) AS average_actual_minus_estimated_days,

            COALESCE(
                SUM(
                    CASE
                        WHEN has_timeline_anomaly = 1 THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS timeline_anomaly_order_count,

            MIN(purchase_date) AS first_observed_purchase_date,
            MAX(purchase_date) AS last_observed_purchase_date

        FROM serving_order_fulfillment

        WHERE {" AND ".join(conditions)}
    """

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
            "履约概览查询没有返回聚合结果。"
        )

    order_count = int(row["order_count"] or 0)
    delivered_order_count = int(
        row["delivered_order_count"] or 0
    )
    canceled_order_count = int(
        row["canceled_order_count"] or 0
    )
    eligible_order_count = int(
        row["delivery_metric_eligible_order_count"] or 0
    )
    late_delivery_count = int(
        row["late_delivery_count"] or 0
    )
    timeline_anomaly_order_count = int(
        row["timeline_anomaly_order_count"] or 0
    )

    return {
        "tool_name": "get_fulfillment_overview",
        "status": (
            "success"
            if order_count > 0
            else "no_data"
        ),
        "source": {
            "type": "mysql_serving_table",
            "table": "serving_order_fulfillment",
            "grain": "每个order_id一行",
            "dataset": "Olist历史电商数据",
            "dataset_purchase_date_coverage": {
                "start_date": DATASET_START_DATE,
                "end_date": DATASET_END_DATE,
            },
        },
        "filters": {
            "start_date": parsed_start_date.isoformat(),
            "end_date": parsed_end_date.isoformat(),
            "customer_state": normalized_state,
            "date_range_rule": "inclusive",
        },
        "observed_data_range": {
            "first_purchase_date": _optional_iso_date(
                row["first_observed_purchase_date"]
            ),
            "last_purchase_date": _optional_iso_date(
                row["last_observed_purchase_date"]
            ),
        },
        "business_rules": {
            "date_field": "purchase_date",
            "delivered_rate_denominator": "order_count",
            "canceled_rate_denominator": "order_count",
            "late_delivery": (
                "实际送达日历日期晚于预计送达日历日期"
            ),
            "late_rate_denominator": (
                "delivery_metric_eligible_order_count"
            ),
            "delivery_metric_eligible": (
                "订单具有计算配送表现所需的实际送达日期和"
                "预计送达日期"
            ),
            "negative_actual_minus_estimated_days": (
                "负数表示早于预计日期送达"
            ),
        },
        "metrics": {
            "order_count": order_count,
            "delivered_order_count": delivered_order_count,
            "delivered_order_rate_pct": _calculate_percentage(
                delivered_order_count,
                order_count,
            ),
            "canceled_order_count": canceled_order_count,
            "canceled_order_rate_pct": _calculate_percentage(
                canceled_order_count,
                order_count,
            ),
            "delivery_metric_eligible_order_count": (
                eligible_order_count
            ),
            "late_delivery_count": late_delivery_count,
            "late_delivery_rate_pct": _calculate_percentage(
                late_delivery_count,
                eligible_order_count,
            ),
            "average_delivery_days": _optional_float(
                row["average_delivery_days"]
            ),
            "average_actual_minus_estimated_days": (
                _optional_float(
                    row[
                        "average_actual_minus_estimated_days"
                    ]
                )
            ),
            "timeline_anomaly_order_count": (
                timeline_anomaly_order_count
            ),
        },
        "limitations": [
            "结果来自历史Olist数据，不代表当前实时业务情况。",
            "工具只能描述履约指标，不能单独证明迟到或取消的原因。",
            "数据不包含退款、仓库库存、物流承运商责任、天气或不可抗力信息。",
            "时间线异常数量用于提示数据或业务流程异常，不应直接解释为物流责任。",
        ],
    }

@mcp_server.tool()
def rank_fulfillment_segments(
    dimension: SegmentDimension,
    rank_metric: RankMetric = "late_delivery_rate_pct",
    start_date: str = DATASET_START_DATE,
    end_date: str = DATASET_END_DATE,
    customer_state: str | None = None,
    minimum_order_count: int = 30,
    limit: int = 10,
) -> dict[str, Any]:
    """
    对履约业务分组进行排名，定位问题集中的范围。

    当用户询问哪些月份、顾客州、商品类别或卖家的迟到率、
    取消率、时间线异常率、平均配送时间或发货截止时间错过率
    较高时使用本工具。

    Args:
        dimension:
            分组维度。支持purchase_month、customer_state、
            product_category和seller。
        rank_metric:
            排名指标。默认按照late_delivery_rate_pct降序排名。
        start_date:
            订单购买开始日期，格式为YYYY-MM-DD。
        end_date:
            订单购买结束日期，格式为YYYY-MM-DD。
        customer_state:
            可选的顾客州过滤条件，例如SP、RJ或MG。
        minimum_order_count:
            分组至少需要包含的订单数量，默认30。
        limit:
            最终返回的排名数量，范围为1到50，默认10。

    Returns:
        包含排名规则、分组指标、数据来源和限制说明的字典。
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
            "start_date不能晚于end_date。"
        )

    normalized_state = _normalize_customer_state(
        customer_state
    )

    if not 1 <= minimum_order_count <= 1_000_000:
        raise ValueError(
            "minimum_order_count必须在1到1000000之间。"
        )

    if not 1 <= limit <= 50:
        raise ValueError(
            "limit必须在1到50之间。"
        )

    if (
        rank_metric == "shipping_limit_missed_rate_pct"
        and dimension not in ITEM_SEGMENT_EXPRESSIONS
    ):
        raise ValueError(
            "shipping_limit_missed_rate_pct只支持"
            "product_category或seller维度。"
        )

    rows = _query_segment_rows(
        dimension=dimension,
        start_date=parsed_start_date,
        end_date=parsed_end_date,
        customer_state=normalized_state,
    )

    segment_records: list[dict[str, Any]] = []

    for row in rows:
        order_count = int(row["order_count"] or 0)

        canceled_order_count = int(
            row["canceled_order_count"] or 0
        )
        eligible_order_count = int(
            row["delivery_metric_eligible_order_count"] or 0
        )
        late_delivery_count = int(
            row["late_delivery_count"] or 0
        )
        timeline_anomaly_order_count = int(
            row["timeline_anomaly_order_count"] or 0
        )

        shipping_eligible_value = row[
            "shipping_limit_eligible_order_count"
        ]
        shipping_missed_value = row[
            "shipping_limit_missed_order_count"
        ]

        if shipping_eligible_value is None:
            shipping_eligible_order_count = None
            shipping_missed_order_count = None
            shipping_missed_rate = None
        else:
            shipping_eligible_order_count = int(
                shipping_eligible_value
            )
            shipping_missed_order_count = int(
                shipping_missed_value or 0
            )
            shipping_missed_rate = _calculate_percentage(
                shipping_missed_order_count,
                shipping_eligible_order_count,
            )

        segment_records.append(
            {
                "segment_value": str(
                    row["segment_value"]
                ),
                "order_count": order_count,
                "canceled_order_count": (
                    canceled_order_count
                ),
                "canceled_order_rate_pct": (
                    _calculate_percentage(
                        canceled_order_count,
                        order_count,
                    )
                ),
                "delivery_metric_eligible_order_count": (
                    eligible_order_count
                ),
                "late_delivery_count": (
                    late_delivery_count
                ),
                "late_delivery_rate_pct": (
                    _calculate_percentage(
                        late_delivery_count,
                        eligible_order_count,
                    )
                ),
                "average_delivery_days": (
                    _optional_float(
                        row["average_delivery_days"]
                    )
                ),
                "timeline_anomaly_order_count": (
                    timeline_anomaly_order_count
                ),
                "timeline_anomaly_rate_pct": (
                    _calculate_percentage(
                        timeline_anomaly_order_count,
                        order_count,
                    )
                ),
                "shipping_limit_eligible_order_count": (
                    shipping_eligible_order_count
                ),
                "shipping_limit_missed_order_count": (
                    shipping_missed_order_count
                ),
                "shipping_limit_missed_rate_pct": (
                    shipping_missed_rate
                ),
            }
        )

    qualified_segments = [
        segment
        for segment in segment_records
        if (
            segment["order_count"] >= minimum_order_count
            and segment[rank_metric] is not None
        )
    ]

    # 先按名称排序，使相同指标值的结果保持稳定。
    qualified_segments.sort(
        key=lambda segment: segment["segment_value"]
    )

    # 再按照用户选择的指标和订单量降序排列。
    qualified_segments.sort(
        key=lambda segment: (
            float(segment[rank_metric]),
            segment["order_count"],
        ),
        reverse=True,
    )

    returned_segments = []

    for rank, segment in enumerate(
        qualified_segments[:limit],
        start=1,
    ):
        returned_segments.append(
            {
                "rank": rank,
                **segment,
            }
        )

    if dimension in ORDER_SEGMENT_COLUMNS:
        source_table = "serving_order_fulfillment"
        source_grain = "每个order_id一行"
    else:
        source_table = "serving_order_item_detail"
        source_grain = (
            "查询时先转换为每个分组值与order_id组合一行"
        )

    limitations = [
        "排名来自历史Olist数据，不代表当前实时业务情况。",
        "排名描述业务信号集中程度，不能单独证明问题原因。",
        "minimum_order_count用于降低极小样本分组排在前列的风险。",
        "不同指标可能使用不同分母，必须结合返回的计数和业务口径解释。",
    ]

    if dimension in ITEM_SEGMENT_EXPRESSIONS:
        limitations.extend(
            [
                "商品类别和卖家排名只包含具有订单明细记录的订单。",
                "一张订单可能关联多个商品类别或卖家，因此不同分组的订单数不能直接相加。",
                "发货截止时间错过表示卖家交给承运环节的时间信号，不等于已经证明卖家造成最终迟到。",
            ]
        )

    return {
        "tool_name": "rank_fulfillment_segments",
        "status": (
            "success"
            if returned_segments
            else "no_data"
        ),
        "source": {
            "type": "mysql_serving_table",
            "table": source_table,
            "query_grain": source_grain,
            "dataset": "Olist历史电商数据",
        },
        "filters": {
            "start_date": parsed_start_date.isoformat(),
            "end_date": parsed_end_date.isoformat(),
            "customer_state": normalized_state,
            "date_range_rule": "inclusive",
            "minimum_order_count": minimum_order_count,
            "limit": limit,
        },
        "ranking": {
            "dimension": dimension,
            "rank_metric": rank_metric,
            "direction": "descending",
            "candidate_segment_count": len(
                segment_records
            ),
            "qualified_segment_count": len(
                qualified_segments
            ),
            "returned_segment_count": len(
                returned_segments
            ),
        },
        "business_rules": {
            "late_delivery": (
                "实际送达日历日期晚于预计送达日历日期"
            ),
            "late_rate_denominator": (
                "delivery_metric_eligible_order_count"
            ),
            "canceled_rate_denominator": "order_count",
            "timeline_anomaly_rate_denominator": (
                "order_count"
            ),
            "shipping_limit_missed_rate_denominator": (
                "shipping_limit_eligible_order_count"
            ),
        },
        "segments": returned_segments,
        "limitations": limitations,
    }

def main() -> None:
    """启动长期运行的 MCP SSE 服务端。"""

    print("模式：电商履约分析 MCP SSE 服务端")
    print(f"监听地址：{MCP_SETTINGS.host}:{MCP_SETTINGS.port}")
    print(f"SSE地址：{MCP_SETTINGS.sse_url}")
    print("已注册MCP工具：")
    print("- get_fulfillment_overview")
    print("- rank_fulfillment_segments")
    print("停止服务：在终端中按 Ctrl+C。")

    mcp_server.run(transport="sse")


if __name__ == "__main__":
    main()