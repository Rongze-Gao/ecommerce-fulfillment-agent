"""电商履约诊断总协调 Agent。

负责：
1. 加载三个业务工具；
2. 由 Agent 自主决定调用哪些工具；
3. 使用 LCEL 汇总全部工具结果；
4. 将最终回答保存到 Redis 对话历史。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import (
    RunnableLambda,
    RunnablePassthrough,
    RunnableWithMessageHistory,
)
from langchain_ollama import ChatOllama

from ecommerce_fulfillment_agent.agent.mcp_client import (
    load_fulfillment_mcp_tools,
)
from ecommerce_fulfillment_agent.agent.memory import (
    get_session_history,
)
from ecommerce_fulfillment_agent.agent.prompts import (
    FINAL_AGENT_EXECUTION_CONTEXT,
    FULFILLMENT_AGENT_SYSTEM_PROMPT,
)
from ecommerce_fulfillment_agent.agent.schemas import (
    FulfillmentAgentResponse,
)
from ecommerce_fulfillment_agent.agent.synthesis import (
    attach_authoritative_tool_evidence,
    calibrate_answer_status,
    create_final_synthesis_chain,
)
from ecommerce_fulfillment_agent.agent_tools.review_evidence import (
    search_review_evidence,
)
from ecommerce_fulfillment_agent.config import (
    get_ollama_settings,
)


EXPECTED_TOOL_NAMES = {
    "get_fulfillment_overview",
    "rank_fulfillment_segments",
    "search_review_evidence",
}


def create_chat_model() -> ChatOllama:
    """创建总协调和最终回答使用的聊天模型。"""

    settings = get_ollama_settings()

    return ChatOllama(
        base_url=settings.base_url,
        model=settings.chat_model,
        temperature=0,
        reasoning=False,
        num_predict=2048,
        keep_alive="10m",
        tags=[
            "olist",
            "fulfillment-agent",
            "ollama",
        ],
    )


async def load_agent_tools() -> list[Any]:
    """加载两个 MCP 工具和一个本地评论检索工具。"""

    mcp_tools = await load_fulfillment_mcp_tools()

    tools = [
        *mcp_tools,
        search_review_evidence,
    ]

    tool_names = {
        tool.name
        for tool in tools
    }

    missing_tool_names = (
        EXPECTED_TOOL_NAMES - tool_names
    )

    if missing_tool_names:
        raise RuntimeError(
            "缺少Agent所需的业务工具："
            f"{sorted(missing_tool_names)}"
        )

    unexpected_tool_names = (
        tool_names - EXPECTED_TOOL_NAMES
    )

    if unexpected_tool_names:
        raise RuntimeError(
            "加载到了未预期的业务工具："
            f"{sorted(unexpected_tool_names)}"
        )

    return tools


def create_fulfillment_agent(
    tools: list[Any],
):
    """创建负责工具决策和调用的总协调 Agent。"""

    model = create_chat_model()

    system_prompt = (
        f"{FULFILLMENT_AGENT_SYSTEM_PROMPT}\n\n"
        f"{FINAL_AGENT_EXECUTION_CONTEXT}"
    )

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
    )


def prepare_agent_input(
    chain_input: dict[str, Any],
) -> dict[str, list[BaseMessage]]:
    """把当前问题和历史消息整理为 Agent 输入。"""

    question = str(
        chain_input.get("question", "")
    ).strip()

    if not question:
        raise ValueError("用户问题不能为空。")

    history = chain_input.get(
        "history",
        [],
    )

    if not isinstance(history, list):
        raise TypeError(
            "history必须是消息列表。"
        )

    return {
        "messages": [
            *history,
            (
                "user",
                question,
            ),
        ]
    }


def extract_business_tool_calls(
    messages: list[BaseMessage],
) -> list[dict[str, Any]]:
    """从Agent执行消息中提取真实发生的业务工具调用。"""

    tool_calls: list[dict[str, Any]] = []

    for message in messages:
        message_tool_calls = getattr(
            message,
            "tool_calls",
            None,
        )

        if not message_tool_calls:
            continue

        for tool_call in message_tool_calls:
            tool_name = tool_call.get("name")

            if tool_name not in EXPECTED_TOOL_NAMES:
                continue

            tool_calls.append(
                {
                    "name": tool_name,
                    "args": tool_call.get(
                        "args",
                        {},
                    ),
                    "id": tool_call.get("id"),
                }
            )

    return tool_calls


def package_synthesized_result(
    agent_result: dict[str, Any],
) -> dict[str, Any]:
    """封装结构化回答，并生成写入Memory的AI消息。"""

    messages = agent_result.get(
        "messages"
    )

    if not isinstance(messages, list):
        raise TypeError(
            "Agent结果中缺少messages列表。"
        )

    response = agent_result.get(
        "structured_response"
    )

    if not isinstance(
        response,
        FulfillmentAgentResponse,
    ):
        raise TypeError(
            "最终综合链没有返回"
            "FulfillmentAgentResponse。"
        )

    # 评论Tool返回的真实评论属于权威证据。
    # 这里直接从ToolMessage提取，避免模型遗漏或改写原文。
    # 从ToolMessage直接重建总体指标、
    # 分组排名和评论三类权威证据。
    response = (
        attach_authoritative_tool_evidence(
            response=response,
            messages=messages,
        )
    )

    response = calibrate_answer_status(
        response=response,
        messages=messages,
    )

    response_json = json.dumps(
        response.model_dump(mode="json"),
        ensure_ascii=False,
    )

    return {
        "structured_response": response,
        "history_message": AIMessage(
            content=response_json,
        ),
        "business_tool_calls": (
            extract_business_tool_calls(
                messages
            )
        ),
    }


def create_coordinator_lcel(
    agent,
):
    """创建总协调 Agent 的完整 LCEL 流水线。

    流程：
    用户问题和历史
    → Agent自主选择并调用工具
    → 汇总所有ToolMessage
    → 生成Pydantic结构化回答
    → 封装Memory消息
    """

    synthesis_chain = (
        create_final_synthesis_chain(
            create_chat_model()
        )
    )

    return (
        RunnableLambda(
            prepare_agent_input
        )
        | agent
        | RunnablePassthrough.assign(
            structured_response=(
                synthesis_chain
            )
        )
        | RunnableLambda(
            package_synthesized_result
        )
    )


def create_memory_fulfillment_chain(
    agent,
):
    """给总协调LCEL增加Redis持久化对话记忆。"""

    coordinator_lcel = (
        create_coordinator_lcel(agent)
    )

    return RunnableWithMessageHistory(
        coordinator_lcel,
        get_session_history=get_session_history,
        input_messages_key="question",
        history_messages_key="history",
        output_messages_key="history_message",
    )


async def create_fulfillment_coordinator():
    """加载工具并创建可直接调用的总协调链。"""

    tools = await load_agent_tools()

    agent = create_fulfillment_agent(
        tools
    )

    memory_chain = (
        create_memory_fulfillment_chain(
            agent
        )
    )

    return memory_chain, tools


async def answer_question(
    memory_chain,
    question: str,
    session_id: str,
) -> dict[str, Any]:
    """在指定会话中向最终Agent提出问题。"""

    normalized_question = " ".join(
        question.split()
    )

    if not normalized_question:
        raise ValueError(
            "用户问题不能为空。"
        )

    normalized_session_id = (
        session_id.strip()
    )

    if not normalized_session_id:
        raise ValueError(
            "session_id不能为空。"
        )

    result = await memory_chain.ainvoke(
        {
            "question": (
                normalized_question
            ),
        },
        config={
            "configurable": {
                "session_id": (
                    normalized_session_id
                ),
            }
        },
    )

    if not isinstance(result, dict):
        raise TypeError(
            "总协调Agent没有返回字典结果。"
        )

    response = result.get(
        "structured_response"
    )

    if not isinstance(
        response,
        FulfillmentAgentResponse,
    ):
        raise TypeError(
            "总协调Agent结果中缺少"
            "FulfillmentAgentResponse。"
        )

    return result