"""连接履约分析 MCP 服务并加载 LangChain 工具。"""

import asyncio
import json
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from ecommerce_fulfillment_agent.config import get_mcp_server_settings


MCP_SETTINGS = get_mcp_server_settings()
FULFILLMENT_SERVER_NAME = "fulfillment"


def create_fulfillment_mcp_client() -> MultiServerMCPClient:
    """创建连接履约分析 SSE 服务的 MCP 客户端。"""

    return MultiServerMCPClient(
        {
            FULFILLMENT_SERVER_NAME: {
                "transport": "sse",
                "url": MCP_SETTINGS.sse_url,
                "timeout": 10.0,
                "sse_read_timeout": 300.0,
            }
        }
    )


async def load_fulfillment_mcp_tools() -> list[BaseTool]:
    """从履约分析 MCP 服务加载 LangChain 兼容工具。"""

    client = create_fulfillment_mcp_client()

    return await client.get_tools(
        server_name=FULFILLMENT_SERVER_NAME
    )


def _extract_json_result(result: object) -> dict[str, Any]:
    """从LangChain MCP工具结果中提取JSON对象。"""

    if isinstance(result, str):
        text_content = result

    elif isinstance(result, list):
        text_parts: list[str] = []

        for block in result:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
            ):
                text = block.get("text")

                if isinstance(text, str):
                    text_parts.append(text)

        if not text_parts:
            raise RuntimeError(
                "MCP工具结果中没有找到文本内容。"
            )

        text_content = "\n".join(text_parts)

    else:
        raise RuntimeError(
            "无法识别MCP工具返回类型："
            f"{type(result).__name__}"
        )

    try:
        parsed_result = json.loads(text_content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "MCP工具返回的文本不是有效JSON。"
        ) from exc

    if not isinstance(parsed_result, dict):
        raise RuntimeError(
            "MCP工具JSON结果不是对象结构。"
        )

    return parsed_result


async def verify_mcp_client() -> None:
    """验证工具发现、LangChain转换和SSE远程调用。"""

    print("模式：履约分析 MCP 客户端端到端验证")
    print(f"服务名称：{FULFILLMENT_SERVER_NAME}")
    print(f"SSE地址：{MCP_SETTINGS.sse_url}")

    print("\n=== 1. 从MCP服务加载工具 ===")

    tools = await load_fulfillment_mcp_tools()

    print(f"加载到的工具数量：{len(tools)}")

    for index, tool in enumerate(tools, start=1):
        print(f"{index}. 工具名称：{tool.name}")
        print(
            "   输入参数："
            + json.dumps(
                tool.args,
                ensure_ascii=False,
            )
        )

    overview_tool = next(
        (
            tool
            for tool in tools
            if tool.name == "get_fulfillment_overview"
        ),
        None,
    )

    ranking_tool = next(
        (
            tool
            for tool in tools
            if tool.name == "rank_fulfillment_segments"
        ),
        None,
    )

    if overview_tool is None:
        raise RuntimeError(
            "MCP服务没有提供get_fulfillment_overview工具。"
        )

    if ranking_tool is None:
        raise RuntimeError(
            "MCP服务没有提供rank_fulfillment_segments工具。"
        )

    print(
        "\n[PASS] 两个MCP工具已经转换为"
        "LangChain BaseTool。"
    )

    print("\n=== 2. 调用履约概览工具 ===")
    print("测试时间范围：2018-01-01 至 2018-06-30")
    print("顾客州：全部州")

    raw_overview_result = await overview_tool.ainvoke(
        {
            "start_date": "2018-01-01",
            "end_date": "2018-06-30",
            "customer_state": None,
        }
    )

    overview_result = _extract_json_result(
        raw_overview_result
    )

    print("\n=== 3. 履约概览JSON结果 ===")
    print(
        json.dumps(
            overview_result,
            ensure_ascii=False,
            indent=2,
        )
    )

    if overview_result.get("status") != "success":
        raise RuntimeError(
            "履约概览工具没有返回success状态。"
        )

    metrics = overview_result.get("metrics")

    if not isinstance(metrics, dict):
        raise RuntimeError(
            "履约概览结果中缺少metrics对象。"
        )

    print("\n=== 4. 履约概览核心指标 ===")
    print(f"订单数量：{metrics['order_count']:,}")
    print(
        "迟到订单数量："
        f"{metrics['late_delivery_count']:,}"
    )
    print(
        "迟到率："
        f"{metrics['late_delivery_rate_pct']}%"
    )
    print(
        "取消率："
        f"{metrics['canceled_order_rate_pct']}%"
    )
    print(
        "平均配送天数："
        f"{metrics['average_delivery_days']}天"
    )

    print(
        "\n[PASS] 履约概览工具已通过SSE返回真实指标。"
    )

    print("\n=== 5. 调用履约分组排名工具 ===")
    print("分组维度：customer_state")
    print("排名指标：late_delivery_rate_pct")
    print("最小订单数：100")
    print("返回数量：5")

    raw_ranking_result = await ranking_tool.ainvoke(
        {
            "dimension": "customer_state",
            "rank_metric": "late_delivery_rate_pct",
            "start_date": "2018-01-01",
            "end_date": "2018-06-30",
            "customer_state": None,
            "minimum_order_count": 100,
            "limit": 5,
        }
    )

    ranking_result = _extract_json_result(
        raw_ranking_result
    )

    print("\n=== 6. 顾客州迟到率排名JSON结果 ===")
    print(
        json.dumps(
            ranking_result,
            ensure_ascii=False,
            indent=2,
        )
    )

    if ranking_result.get("status") != "success":
        raise RuntimeError(
            "履约分组排名工具没有返回success状态。"
        )

    segments = ranking_result.get("segments")

    if not isinstance(segments, list) or not segments:
        raise RuntimeError(
            "履约分组排名工具没有返回有效分组。"
        )

    ranking_values: list[float] = []

    print("\n=== 7. 顾客州迟到率前5名 ===")

    for segment in segments:
        if not isinstance(segment, dict):
            raise RuntimeError(
                "分组排名中存在无效结果结构。"
            )

        rate = segment.get("late_delivery_rate_pct")

        if not isinstance(rate, int | float):
            raise RuntimeError(
                "分组排名结果缺少迟到率。"
            )

        ranking_values.append(float(rate))

        print(
            f"{segment['rank']}. "
            f"{segment['segment_value']}州 | "
            f"迟到率={rate}% | "
            f"迟到订单={segment['late_delivery_count']:,} | "
            f"可计算订单="
            f"{segment['delivery_metric_eligible_order_count']:,}"
        )

    if ranking_values != sorted(
        ranking_values,
        reverse=True,
    ):
        raise RuntimeError(
            "分组结果没有按照迟到率降序排列。"
        )

    print(
        "\n[PASS] 履约分组排名工具已完成"
        "MySQL查询、SSE调用和降序排名验证。"
    )

    print(
        "\n=== 8. 调用商品类别发货截止时间错过率排名 ==="
    )
    print("分组维度：product_category")
    print("排名指标：shipping_limit_missed_rate_pct")
    print("最小订单数：100")
    print("返回数量：5")

    raw_category_result = await ranking_tool.ainvoke(
        {
            "dimension": "product_category",
            "rank_metric": (
                "shipping_limit_missed_rate_pct"
            ),
            "start_date": "2018-01-01",
            "end_date": "2018-06-30",
            "customer_state": None,
            "minimum_order_count": 100,
            "limit": 5,
        }
    )

    category_result = _extract_json_result(
        raw_category_result
    )

    if category_result.get("status") != "success":
        raise RuntimeError(
            "商品类别排名没有返回success状态。"
        )

    source = category_result.get("source")

    if (
        not isinstance(source, dict)
        or source.get("table")
        != "serving_order_item_detail"
    ):
        raise RuntimeError(
            "商品类别排名没有使用订单明细Serving表。"
        )

    category_segments = category_result.get("segments")

    if (
        not isinstance(category_segments, list)
        or not category_segments
    ):
        raise RuntimeError(
            "商品类别排名没有返回有效分组。"
        )

    category_rates: list[float] = []

    print(
        "\n=== 9. 商品类别发货截止时间错过率前5名 ==="
    )

    for segment in category_segments:
        if not isinstance(segment, dict):
            raise RuntimeError(
                "商品类别排名中存在无效结果结构。"
            )

        rate = segment.get(
            "shipping_limit_missed_rate_pct"
        )
        eligible_count = segment.get(
            "shipping_limit_eligible_order_count"
        )
        missed_count = segment.get(
            "shipping_limit_missed_order_count"
        )

        if not isinstance(rate, int | float):
            raise RuntimeError(
                "商品类别排名缺少发货截止时间错过率。"
            )

        if not isinstance(eligible_count, int):
            raise RuntimeError(
                "商品类别排名缺少可计算订单数量。"
            )

        if not isinstance(missed_count, int):
            raise RuntimeError(
                "商品类别排名缺少错过截止时间的订单数量。"
            )

        category_rates.append(float(rate))

        print(
            f"{segment['rank']}. "
            f"{segment['segment_value']} | "
            f"错过率={rate}% | "
            f"错过订单={missed_count:,} | "
            f"可计算订单={eligible_count:,}"
        )

    if category_rates != sorted(
        category_rates,
        reverse=True,
    ):
        raise RuntimeError(
            "商品类别结果没有按照错过率降序排列。"
        )

    print(
        "\n[PASS] 商品类别排名已完成订单明细去重、"
        "发货截止时间计算和降序验证。"
    )


def main() -> None:
    """运行MCP客户端验证程序。"""

    try:
        asyncio.run(verify_mcp_client())
    except Exception as exc:
        print(
            "\n[FAIL] MCP客户端验证失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()