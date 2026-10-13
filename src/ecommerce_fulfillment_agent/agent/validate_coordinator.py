"""验证总协调Agent能完整综合三个业务工具的结果。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ecommerce_fulfillment_agent.agent.fulfillment_agent import (
    answer_question,
    create_fulfillment_coordinator,
)
from ecommerce_fulfillment_agent.agent.memory import (
    clear_session_history,
    get_session_history,
)
from ecommerce_fulfillment_agent.agent.schemas import (
    FulfillmentAgentResponse,
)
from ecommerce_fulfillment_agent.config import (
    get_ollama_settings,
)


TEST_SESSION_ID = (
    "fulfillment-coordinator-validation-001"
)

TEST_QUESTION = (
    "请分析2018年1月1日至2018年6月30日的"
    "整体订单迟到率；然后在订单量至少100单的"
    "顾客州中，列出迟到率最高的前3个州；"
    "最后从全部历史顾客评论语料中找出5条"
    "与迟到送达有关的真实评论，作为定性案例。"
    "请明确区分指定时间范围内的统计指标与"
    "全量评论语料中的候选案例，评论案例不能"
    "代表总体投诉比例。"
)


def print_tool_calls(
    tool_calls: list[dict[str, Any]],
) -> None:
    """打印Agent实际选择的业务工具及参数。"""

    print("\n=== 2. Agent工具决策 ===")

    if not tool_calls:
        print("[WARN] Agent没有调用任何业务工具。")
        return

    for position, tool_call in enumerate(
        tool_calls,
        start=1,
    ):
        arguments = json.dumps(
            tool_call.get("args", {}),
            ensure_ascii=False,
        )

        print(
            f"{position}. "
            f"{tool_call.get('name')}"
        )
        print(f"   参数：{arguments}")


def print_structured_response(
    response: FulfillmentAgentResponse,
) -> None:
    """打印最终Pydantic结构化回答。"""

    print("\n=== 3. 最终结构化回答 ===")

    print(
        json.dumps(
            response.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )


def print_saved_history(
    session_id: str,
) -> None:
    """打印Redis中最终保存的对话历史。"""

    history = get_session_history(
        session_id
    )

    print("\n=== 4. Redis对话历史 ===")
    print(f"session_id：{session_id}")
    print(
        f"已保存消息数："
        f"{len(history.messages)}"
    )

    for position, message in enumerate(
        history.messages,
        start=1,
    ):
        content = " ".join(
            str(message.content).split()
        )

        if len(content) > 180:
            content = f"{content[:180]}..."

        print(
            f"{position}. "
            f"{message.__class__.__name__}："
            f"{content}"
        )


def validate_tool_calls(
    tool_calls: list[dict[str, Any]],
) -> None:
    """验证三个业务工具及其参数。"""

    expected_tool_names = {
        "get_fulfillment_overview",
        "rank_fulfillment_segments",
        "search_review_evidence",
    }

    actual_tool_names = [
        tool_call["name"]
        for tool_call in tool_calls
    ]

    assert set(actual_tool_names) == (
        expected_tool_names
    ), (
        "Agent没有正确调用全部三个业务工具："
        f"{actual_tool_names}"
    )

    assert len(actual_tool_names) == 3, (
        "每个业务工具应只调用一次，"
        f"当前调用记录：{actual_tool_names}"
    )

    calls_by_name = {
        tool_call["name"]: tool_call
        for tool_call in tool_calls
    }

    overview_args = calls_by_name[
        "get_fulfillment_overview"
    ]["args"]

    assert overview_args.get(
        "start_date"
    ) == "2018-01-01"

    assert overview_args.get(
        "end_date"
    ) == "2018-06-30"

    ranking_args = calls_by_name[
        "rank_fulfillment_segments"
    ]["args"]

    assert ranking_args.get(
        "dimension"
    ) == "customer_state"

    assert ranking_args.get(
        "start_date"
    ) == "2018-01-01"

    assert ranking_args.get(
        "end_date"
    ) == "2018-06-30"

    assert ranking_args.get(
        "minimum_order_count"
    ) == 100

    assert ranking_args.get(
        "limit"
    ) == 3

    # rank_metric具有默认值时，模型可能不显式传入。
    rank_metric = ranking_args.get(
        "rank_metric",
        "late_delivery_rate_pct",
    )

    assert (
        rank_metric
        == "late_delivery_rate_pct"
    )

    review_args = calls_by_name[
        "search_review_evidence"
    ]["args"]

    assert review_args.get("k") == 5

    review_question = str(
        review_args.get("question", "")
    ).strip()

    assert review_question, (
        "评论检索工具没有收到有效问题。"
    )

    print(
        "\n[PASS] 总协调Agent正确调用了"
        "全部三个业务工具，且每个工具只调用一次。"
    )
    print(
        "[PASS] 三个工具的日期、维度、"
        "样本门槛和返回数量参数正确。"
    )


def validate_final_response(
    response: FulfillmentAgentResponse,
) -> None:
    """验证最终回答完整保留三路工具证据。"""

    assert (
        response.answer_status
        == "answered"
    ), (
        "三个工具均成功返回结果时，"
        "最终状态应为answered，"
        f"实际为{response.answer_status}。"
    )

    response_data = response.model_dump(
        mode="json"
    )

    response_text = json.dumps(
        response_data,
        ensure_ascii=False,
    )

    required_fragments = [
        "8.63",
        "AL",
        "25.0",
        "CE",
        "24.29",
        "SE",
        "22.68",
    ]

    missing_fragments = [
        fragment
        for fragment in required_fragments
        if fragment not in response_text
    ]

    assert not missing_fragments, (
        "最终回答没有完整使用总体指标或"
        "州排名结果，缺少："
        f"{missing_fragments}"
    )

    review_evidence = [
        evidence
        for evidence in response.evidence
        if (
            "评论文档ID=" in evidence
            and "葡萄牙语原文：" in evidence
        )
    ]

    assert len(review_evidence) == 5, (
        "最终回答应恰好保留5条"
        "权威评论证据，当前数量为："
        f"{len(review_evidence)}"
    )

    limitation_text = " ".join(
        response.limitations
    )

    assert (
        "不能代表" in limitation_text
        or "不得" in limitation_text
        or "不能直接" in limitation_text
    ), (
        "最终回答没有说明Top-K评论"
        "不能代表总体投诉比例。"
    )

    print(
        "[PASS] 最终回答保留了"
        "2018年上半年8.63%的整体迟到率。"
    )
    print(
        "[PASS] 最终回答保留了"
        "AL、CE、SE三个州的排名结果。"
    )
    print(
        "[PASS] 最终回答恰好保留了"
        "5条真实顾客评论证据。"
    )
    print(
        "[PASS] 最终回答明确区分了"
        "统计指标与评论候选案例。"
    )


def validate_memory(
    session_id: str,
) -> None:
    """验证Redis只保存用户问题和最终AI回答。"""

    history = get_session_history(
        session_id
    )

    assert len(history.messages) == 2, (
        "Redis Memory应只保存一条用户消息"
        "和一条最终AI消息，当前消息数为："
        f"{len(history.messages)}"
    )

    assert (
        history.messages[0].type
        == "human"
    )

    assert (
        history.messages[1].type
        == "ai"
    )

    print(
        "[PASS] Redis Memory只保存了"
        "用户问题和最终结构化回答。"
    )


async def main() -> None:
    """运行三工具复合问题端到端验证。"""

    settings = get_ollama_settings()

    # 仅删除本次验证使用的会话，
    # 不影响Redis DB 0中的向量数据，
    # 也不影响其他对话会话。
    clear_session_history(
        TEST_SESSION_ID
    )

    print(
        "模式：履约诊断总协调Agent复合验证"
    )
    print(
        "当前能力：System Prompt + "
        "ChatOllama + Pydantic + "
        "Redis Memory + 3个业务工具"
    )
    print(
        "协调方式：create_agent自主选择工具，"
        "LCEL统一综合全部ToolMessage"
    )
    print(
        f"Ollama地址：{settings.base_url}"
    )
    print(
        f"回答模型：{settings.chat_model}"
    )
    print(
        f"session_id：{TEST_SESSION_ID}"
    )

    print("\n=== 1. 创建总协调Agent ===")

    memory_chain, tools = (
        await create_fulfillment_coordinator()
    )

    print(
        f"总协调Agent可用工具数："
        f"{len(tools)}"
    )

    for position, tool in enumerate(
        tools,
        start=1,
    ):
        print(
            f"{position}. {tool.name}"
        )

    print(
        f"\n用户复合问题：{TEST_QUESTION}"
    )

    result = await answer_question(
        memory_chain=memory_chain,
        question=TEST_QUESTION,
        session_id=TEST_SESSION_ID,
    )

    tool_calls = result[
        "business_tool_calls"
    ]

    response = result[
        "structured_response"
    ]

    print_tool_calls(
        tool_calls
    )

    print_structured_response(
        response
    )

    print_saved_history(
        TEST_SESSION_ID
    )

    print("\n=== 5. 自动验证 ===")

    validate_tool_calls(
        tool_calls
    )

    validate_final_response(
        response
    )

    validate_memory(
        TEST_SESSION_ID
    )

    print(
        "\n[PASS] 三工具复合问题已经完成"
        "工具决策、结果综合、结构化输出和"
        "Redis记忆端到端验证。"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(
            "\n[FAIL] 总协调Agent复合验证失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc