"""将多个业务工具结果综合为最终结构化回答。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.prompts import (
    ChatPromptTemplate,
)
from langchain_core.runnables import (
    RunnableLambda,
)

from ecommerce_fulfillment_agent.agent.prompts import (
    FINAL_AGENT_EXECUTION_CONTEXT,
    FULFILLMENT_AGENT_SYSTEM_PROMPT,
)
from ecommerce_fulfillment_agent.agent.schemas import (
    FulfillmentAgentResponse,
)


FINAL_SYNTHESIS_PROMPT = (
    ChatPromptTemplate.from_messages(
        [
            (
                "system",
                FULFILLMENT_AGENT_SYSTEM_PROMPT,
            ),
            (
                "system",
                FINAL_AGENT_EXECUTION_CONTEXT,
            ),
            (
                "system",
                """
你负责根据本轮全部业务工具结果生成最终回答，
不再进行工具选择。

【基本规则】

1. 必须检查tool_results中的每个工具结果。
2. 不得只使用最后一个工具的结果。
3. 工具结果是业务事实的唯一来源。
4. agent_draft只作为表达参考，不能覆盖工具事实。
5. 不得编造工具结果中不存在的指标、类别、卖家或评论。
6. 不得把发货截止时间错过直接解释为最终配送迟到。
7. 不得把统计关联解释为问题根本原因。

【三个工具的职责】

1. get_fulfillment_overview：
   提供指定范围内的总体履约指标。

2. rank_fulfillment_segments：
   提供指定维度和指标的真实分组排名。

3. search_review_evidence：
   提供语义相近的真实顾客评论候选案例。

【排名回答规则】

1. 用户询问排名时，summary应概括排名结论。
2. findings必须说明排名靠前的主要分组及其指标值。
3. 不得声称“以下是前N名”却不提供任何排名内容。
4. 只回答工具结果中的dimension和rank_metric。
5. 不得把商品类别排名扩展成卖家排名。
6. 不得把单一指标扩展成取消率、配送天数等其他指标。
7. 完整排名证据由程序直接从ToolMessage写入evidence，
   模型不要自行复制完整排名列表。

【建议规则】

1. 用户明确要求建议时，next_steps不能为空。
2. 建议必须针对本次实际查询的指标和分组。
3. 对发货截止时间错过率问题，可以建议：
   - 对高风险类别设置截止时间预警；
   - 继续定位相关类别中问题集中的卖家；
   - 同时检查错过订单数和可计算订单数；
   - 将发货截止时间错过与最终迟到分别监控。
4. 在没有相关数据时，不得断言问题由库存、
   仓库、承运商或卖家能力造成。
5. 如果建议依赖项目没有的数据，必须明确说明
   需要补充相应数据后才能验证。

【评论规则】

1. 评论属于语义相近的候选案例。
2. 评论不能代表总体投诉比例。
3. 评论不能单独证明业务因果关系。
4. 评论原文由程序直接写入evidence，
   模型不要重复复制评论列表。

【回答状态】

1. 用户要求的指标、排名或评论已经由工具完整返回时，
   answer_status应为answered。
2. 工具失败、结果为空、结果数量不足，或者无法回答
   用户主要问题时，返回partially_answered。
3. 用户要求证明原因，但工具只能提供关联时，
   返回partially_answered。
4. 问题核心依赖项目完全没有的数据时，
   返回out_of_scope。

【字段要求】

1. summary：直接回答用户问题的总体结论。
2. findings：保存由工具证据支持的主要业务发现。
3. evidence：可以暂时为空，程序会写入权威工具证据。
4. limitations：保存数据范围、统计口径和因果限制。
5. next_steps：保存针对本次结果的可执行后续分析建议。
6. 不展示内部思维过程。
""".strip(),
            ),
            (
                "human",
                """
【用户当前问题】

{question}

【Agent初步回答】

{agent_draft}

【本轮全部业务工具结果】

{tool_results}

请根据全部成功的工具结果生成最终结构化回答。
""".strip(),
            ),
        ]
    )
)


def parse_tool_message_payload(
    message: ToolMessage,
) -> dict[str, Any] | None:
    """将ToolMessage内容解析为JSON字典。"""

    content = message.content

    if isinstance(content, str):
        content_text = content

    elif isinstance(content, list):
        text_parts: list[str] = []

        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
                continue

            if isinstance(item, dict):
                text_value = item.get("text")

                if isinstance(text_value, str):
                    text_parts.append(
                        text_value
                    )

        content_text = "\n".join(
            text_parts
        )

    else:
        return None

    content_text = content_text.strip()

    if not content_text:
        return None

    try:
        payload = json.loads(
            content_text
        )
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    return payload


def extract_tool_payloads(
    messages: list[BaseMessage],
) -> list[dict[str, Any]]:
    """提取本轮全部可解析的ToolMessage。"""

    payloads: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(
            message,
            ToolMessage,
        ):
            continue

        payload = parse_tool_message_payload(
            message
        )

        if payload is not None:
            payloads.append(payload)

    return payloads


def extract_latest_question(
    messages: list[BaseMessage],
) -> str:
    """提取当前轮次最近的用户问题。"""

    for message in reversed(messages):
        if not isinstance(
            message,
            HumanMessage,
        ):
            continue

        content = message.content

        if (
            isinstance(content, str)
            and content.strip()
        ):
            return content.strip()

    raise RuntimeError(
        "Agent执行轨迹中没有找到用户问题。"
    )


def extract_agent_draft(
    messages: list[BaseMessage],
) -> str:
    """提取Agent执行工具后的初步回答。"""

    for message in reversed(messages):
        if not isinstance(
            message,
            AIMessage,
        ):
            continue

        if message.tool_calls:
            continue

        content = message.content

        if (
            isinstance(content, str)
            and content.strip()
        ):
            return content.strip()

    return ""


def compact_review(
    review: dict[str, Any],
) -> dict[str, Any]:
    """保留最终综合真正需要的评论字段。"""

    field_names = (
        "rank",
        "parent_document_id",
        "order_id",
        "review_score",
        "customer_state",
        "order_status",
        "delivery_timing_label",
        "has_timeline_anomaly",
        "similarity_score",
        "review_text",
    )

    return {
        field_name: review.get(
            field_name
        )
        for field_name in field_names
    }


def get_ranking_metric_fields(
    rank_metric: str,
) -> tuple[str, ...]:
    """返回某个排名指标真正需要的segment字段。"""

    metric_fields = {
        "late_delivery_rate_pct": (
            "delivery_metric_eligible_order_count",
            "late_delivery_count",
            "late_delivery_rate_pct",
        ),
        "canceled_order_rate_pct": (
            "canceled_order_count",
            "canceled_order_rate_pct",
        ),
        "timeline_anomaly_rate_pct": (
            "timeline_anomaly_order_count",
            "timeline_anomaly_rate_pct",
        ),
        "average_delivery_days": (
            "delivery_metric_eligible_order_count",
            "average_delivery_days",
        ),
        "shipping_limit_missed_rate_pct": (
            "shipping_limit_eligible_order_count",
            "shipping_limit_missed_order_count",
            "shipping_limit_missed_rate_pct",
        ),
        "order_count": (
            "order_count",
        ),
    }

    return metric_fields.get(
        rank_metric,
        (),
    )


def compact_ranking_segment(
    segment: dict[str, Any],
    rank_metric: str,
) -> dict[str, Any]:
    """只保留当前排名指标需要的分组字段。"""

    compact_segment: dict[str, Any] = {
        "rank": segment.get("rank"),
        "segment_value": segment.get(
            "segment_value"
        ),
        "order_count": segment.get(
            "order_count"
        ),
    }

    for field_name in (
        get_ranking_metric_fields(
            rank_metric
        )
    ):
        compact_segment[field_name] = (
            segment.get(field_name)
        )

    return compact_segment


def compact_tool_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """压缩工具结果，减少无关字段对模型的干扰。"""

    tool_name = payload.get(
        "tool_name"
    )

    if tool_name == (
        "get_fulfillment_overview"
    ):
        return {
            "tool_name": tool_name,
            "status": payload.get("status"),
            "filters": payload.get("filters"),
            "observed_data_range": payload.get(
                "observed_data_range"
            ),
            "business_rules": payload.get(
                "business_rules"
            ),
            "metrics": payload.get("metrics"),
            "limitations": payload.get(
                "limitations"
            ),
        }

    if tool_name == (
        "rank_fulfillment_segments"
    ):
        ranking = payload.get(
            "ranking",
            {},
        )

        if not isinstance(ranking, dict):
            ranking = {}

        rank_metric = str(
            ranking.get(
                "rank_metric",
                "",
            )
        )

        segments = payload.get(
            "segments",
            [],
        )

        if not isinstance(segments, list):
            segments = []

        compact_segments = [
            compact_ranking_segment(
                segment,
                rank_metric,
            )
            for segment in segments
            if isinstance(segment, dict)
        ]

        return {
            "tool_name": tool_name,
            "status": payload.get("status"),
            "filters": payload.get("filters"),
            "ranking": ranking,
            "segments": compact_segments,
            "limitations": payload.get(
                "limitations"
            ),
        }

    if tool_name == (
        "search_review_evidence"
    ):
        reviews = payload.get(
            "reviews",
            [],
        )

        if not isinstance(reviews, list):
            reviews = []

        compact_reviews = [
            compact_review(review)
            for review in reviews
            if isinstance(review, dict)
        ]

        return {
            "tool_name": tool_name,
            "status": payload.get("status"),
            "question": payload.get("question"),
            "result_count": payload.get(
                "result_count"
            ),
            "reviews": compact_reviews,
            "limitations": payload.get(
                "limitations"
            ),
        }

    return {
        "tool_name": tool_name,
        "status": payload.get("status"),
    }


def extract_compact_tool_results(
    messages: list[BaseMessage],
) -> list[dict[str, Any]]:
    """生成提供给最终模型的紧凑工具证据包。"""

    return [
        compact_tool_payload(payload)
        for payload in extract_tool_payloads(
            messages
        )
    ]


def build_synthesis_input(
    agent_result: dict[str, Any],
) -> dict[str, str]:
    """将Agent执行结果转换为综合Prompt输入。"""

    messages = agent_result.get(
        "messages"
    )

    if not isinstance(messages, list):
        raise TypeError(
            "Agent结果中缺少messages列表。"
        )

    tool_results = (
        extract_compact_tool_results(
            messages
        )
    )

    # 有真实工具结果时，避免残缺的Agent草稿
    # 干扰最终模型对工具结果的使用。
    if tool_results:
        agent_draft = ""
    else:
        agent_draft = extract_agent_draft(
            messages
        )

    return {
        "question": extract_latest_question(
            messages
        ),
        "agent_draft": agent_draft,
        "tool_results": json.dumps(
            tool_results,
            ensure_ascii=False,
            default=str,
        ),
    }


def create_final_synthesis_chain(
    model,
):
    """创建最终证据综合LCEL。"""

    structured_model = (
        model.with_structured_output(
            FulfillmentAgentResponse,
            method="json_schema",
        )
    )

    # 明确的LCEL：
    # Agent执行结果
    # → 紧凑工具证据
    # → 最终Prompt
    # → Pydantic结构化回答
    return (
        RunnableLambda(
            build_synthesis_input
        )
        | FINAL_SYNTHESIS_PROMPT
        | structured_model
    )


def format_scope(
    filters: dict[str, Any],
) -> str:
    """把工具筛选条件格式化为证据文本。"""

    start_date = filters.get(
        "start_date",
        "未指定",
    )
    end_date = filters.get(
        "end_date",
        "未指定",
    )
    customer_state = filters.get(
        "customer_state"
    )

    state_text = (
        str(customer_state)
        if customer_state
        else "全部州"
    )

    return (
        f"时间范围={start_date}至{end_date}"
        f" | 顾客州={state_text}"
    )


def extract_overview_evidence(
    payload: dict[str, Any],
) -> list[str]:
    """把履约概览结果转换为权威证据。"""

    filters = payload.get(
        "filters",
        {},
    )
    metrics = payload.get(
        "metrics",
        {},
    )

    if not isinstance(filters, dict):
        filters = {}

    if not isinstance(metrics, dict):
        return []

    if not metrics:
        return []

    return [
        (
            "履约概览"
            f" | {format_scope(filters)}"
            " | 订单数="
            f"{metrics.get('order_count')}"
            " | 妥投率="
            f"{metrics.get('delivered_order_rate_pct')}%"
            " | 取消率="
            f"{metrics.get('canceled_order_rate_pct')}%"
            " | 可计算配送订单数="
            f"{metrics.get('delivery_metric_eligible_order_count')}"
            " | 迟到订单数="
            f"{metrics.get('late_delivery_count')}"
            " | 迟到率="
            f"{metrics.get('late_delivery_rate_pct')}%"
            " | 平均配送天数="
            f"{metrics.get('average_delivery_days')}"
            " | 时间线异常订单数="
            f"{metrics.get('timeline_anomaly_order_count')}"
        )
    ]


def format_metric_value(
    rank_metric: str,
    value: Any,
) -> str:
    """根据指标类型格式化排名值。"""

    if value is None:
        return "NULL"

    if rank_metric.endswith(
        "_pct"
    ):
        return f"{value}%"

    if rank_metric == (
        "average_delivery_days"
    ):
        return f"{value}天"

    return str(value)


def extract_ranking_evidence(
    payload: dict[str, Any],
) -> list[str]:
    """把全部真实分组排名转换为权威证据。"""

    ranking = payload.get(
        "ranking",
        {},
    )
    filters = payload.get(
        "filters",
        {},
    )
    segments = payload.get(
        "segments",
        [],
    )

    if not isinstance(ranking, dict):
        return []

    if not isinstance(filters, dict):
        filters = {}

    if not isinstance(segments, list):
        return []

    dimension = str(
        ranking.get(
            "dimension",
            "unknown",
        )
    )
    rank_metric = str(
        ranking.get(
            "rank_metric",
            "unknown",
        )
    )

    evidence: list[str] = [
        (
            "分组排名口径"
            f" | {format_scope(filters)}"
            f" | 维度={dimension}"
            f" | 指标={rank_metric}"
            " | 最小订单数="
            f"{filters.get('minimum_order_count')}"
            " | 返回数量="
            f"{ranking.get('returned_segment_count')}"
        )
    ]

    for segment in segments:
        if not isinstance(segment, dict):
            continue

        metric_value = segment.get(
            rank_metric
        )

        line = (
            f"排名{segment.get('rank')}"
            f" | {dimension}="
            f"{segment.get('segment_value')}"
            f" | {rank_metric}="
            f"{format_metric_value(rank_metric, metric_value)}"
            " | 订单数="
            f"{segment.get('order_count')}"
        )

        if rank_metric == (
            "late_delivery_rate_pct"
        ):
            line += (
                " | 迟到订单数="
                f"{segment.get('late_delivery_count')}"
                " | 可计算配送订单数="
                f"{segment.get('delivery_metric_eligible_order_count')}"
            )

        elif rank_metric == (
            "canceled_order_rate_pct"
        ):
            line += (
                " | 取消订单数="
                f"{segment.get('canceled_order_count')}"
            )

        elif rank_metric == (
            "timeline_anomaly_rate_pct"
        ):
            line += (
                " | 时间线异常订单数="
                f"{segment.get('timeline_anomaly_order_count')}"
            )

        elif rank_metric == (
            "shipping_limit_missed_rate_pct"
        ):
            line += (
                " | 错过截止时间订单数="
                f"{segment.get('shipping_limit_missed_order_count')}"
                " | 可计算发货截止时间订单数="
                f"{segment.get('shipping_limit_eligible_order_count')}"
            )

        evidence.append(line)

    return evidence


def extract_review_evidence(
    payload: dict[str, Any],
) -> list[str]:
    """把真实评论转换为权威证据。"""

    reviews = payload.get(
        "reviews",
        [],
    )

    if not isinstance(reviews, list):
        return []

    evidence: list[str] = []

    for review in reviews:
        if not isinstance(review, dict):
            continue

        document_id = review.get(
            "parent_document_id"
        )
        order_id = review.get(
            "order_id"
        )
        review_text = review.get(
            "review_text"
        )

        if not all(
            isinstance(value, str)
            and value.strip()
            for value in (
                document_id,
                order_id,
                review_text,
            )
        ):
            continue

        evidence.append(
            f"评论文档ID={document_id}"
            f" | 订单ID={order_id}"
            " | 评分="
            f"{review.get('review_score')}"
            " | 顾客州="
            f"{review.get('customer_state')}"
            " | 订单状态="
            f"{review.get('order_status')}"
            " | 配送标签="
            f"{review.get('delivery_timing_label')}"
            " | 相似度="
            f"{review.get('similarity_score')}"
            " | 葡萄牙语原文："
            f"{review_text.strip()}"
        )

    return evidence


def attach_authoritative_tool_evidence(
    response: FulfillmentAgentResponse,
    messages: list[BaseMessage],
) -> FulfillmentAgentResponse:
    """使用ToolMessage重建全部权威证据。

    最终模型负责解释；
    Python负责确保真实数据不会被遗漏或改写。
    """

    authoritative_evidence: list[str] = []

    for payload in extract_tool_payloads(
        messages
    ):
        if payload.get("status") != "success":
            continue

        tool_name = payload.get(
            "tool_name"
        )

        if tool_name == (
            "get_fulfillment_overview"
        ):
            authoritative_evidence.extend(
                extract_overview_evidence(
                    payload
                )
            )

        elif tool_name == (
            "rank_fulfillment_segments"
        ):
            authoritative_evidence.extend(
                extract_ranking_evidence(
                    payload
                )
            )

        elif tool_name == (
            "search_review_evidence"
        ):
            authoritative_evidence.extend(
                extract_review_evidence(
                    payload
                )
            )

    if not authoritative_evidence:
        return response

    return response.model_copy(
        update={
            "evidence": authoritative_evidence,
        }
    )


def is_tool_payload_complete(
    payload: dict[str, Any],
) -> bool:
    """检查一个成功工具结果是否包含足够数据。"""

    if payload.get("status") != "success":
        return False

    tool_name = payload.get(
        "tool_name"
    )

    if tool_name == (
        "get_fulfillment_overview"
    ):
        metrics = payload.get("metrics")

        return (
            isinstance(metrics, dict)
            and bool(metrics)
        )

    if tool_name == (
        "rank_fulfillment_segments"
    ):
        ranking = payload.get(
            "ranking",
            {},
        )
        segments = payload.get(
            "segments",
            [],
        )

        if not isinstance(ranking, dict):
            return False

        if not isinstance(segments, list):
            return False

        returned_count = ranking.get(
            "returned_segment_count",
            0,
        )

        return (
            isinstance(returned_count, int)
            and returned_count > 0
            and len(segments) == returned_count
        )

    if tool_name == (
        "search_review_evidence"
    ):
        result_count = payload.get(
            "result_count",
            0,
        )
        reviews = payload.get(
            "reviews",
            [],
        )

        return (
            isinstance(result_count, int)
            and result_count > 0
            and isinstance(reviews, list)
            and len(reviews) == result_count
        )

    return False


def calibrate_answer_status(
    response: FulfillmentAgentResponse,
    messages: list[BaseMessage],
) -> FulfillmentAgentResponse:
    """根据真实工具结果校准最终回答状态。"""

    if response.answer_status != (
        "partially_answered"
    ):
        return response

    user_question = extract_latest_question(
        messages
    ).lower()

    causal_terms = (
        "为什么",
        "根本原因",
        "导致",
        "造成",
        "责任",
        "归因",
        "证明",
        "why",
        "cause",
        "responsible",
    )

    if any(
        term in user_question
        for term in causal_terms
    ):
        return response

    payloads = extract_tool_payloads(
        messages
    )

    if not payloads:
        return response

    if not all(
        is_tool_payload_complete(payload)
        for payload in payloads
    ):
        return response

    return response.model_copy(
        update={
            "answer_status": "answered",
        }
    )