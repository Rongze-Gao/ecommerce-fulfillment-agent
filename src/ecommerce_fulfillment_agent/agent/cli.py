"""Olist 电商履约诊断 Agent 命令行入口。

启动前需已运行 MCP SSE 服务、MySQL、Redis 与 Ollama。
同一 --session-id 会读取 Redis 中已有对话历史。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import json
from typing import Any

from ecommerce_fulfillment_agent.agent.fulfillment_agent import (
    answer_question,
    create_fulfillment_coordinator,
)
from ecommerce_fulfillment_agent.agent.memory import (
    clear_session_history,
)
from ecommerce_fulfillment_agent.agent.schemas import (
    FulfillmentAgentResponse,
)


DEFAULT_SESSION_ID = (
    "ecommerce-fulfillment-cli"
)

EXIT_COMMANDS = {
    "exit",
    "quit",
    "/exit",
    "/quit",
    "退出",
    "结束",
}


def parse_arguments() -> argparse.Namespace:
    """读取命令行参数。"""

    parser = argparse.ArgumentParser(
        description=(
            "Olist电商履约诊断Agent"
            "命令行对话程序"
        )
    )

    parser.add_argument(
        "--session-id",
        default=DEFAULT_SESSION_ID,
        help=(
            "Redis对话会话ID；"
            "使用相同ID可以继续之前的对话。"
        ),
    )

    return parser.parse_args()


async def show_thinking(
    stop_event: asyncio.Event,
) -> None:
    """在Agent运行期间显示等待动画。

    这里只表示程序正在调用模型和工具，
    不展示模型内部思维过程。
    """

    frames = [
        "Thinking",
        "Thinking.",
        "Thinking..",
        "Thinking...",
    ]

    position = 0

    try:
        while not stop_event.is_set():
            frame = frames[
                position % len(frames)
            ]

            sys.stdout.write(
                f"\r{frame:<16}"
            )
            sys.stdout.flush()

            position += 1

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=0.4,
                )
            except asyncio.TimeoutError:
                continue
    finally:
        sys.stdout.write(
            "\r" + " " * 20 + "\r"
        )
        sys.stdout.flush()


def print_section(
    title: str,
    values: list[str],
) -> None:
    """打印非空的回答列表字段。"""

    if not values:
        return

    print(f"\n{title}")

    for position, value in enumerate(
        values,
        start=1,
    ):
        print(f"{position}. {value}")


def print_agent_response(
    response: FulfillmentAgentResponse,
) -> None:
    """以适合用户阅读的方式显示Agent回答。"""

    print("\nAgent：")
    print(response.summary)

    print_section(
        "主要发现：",
        response.findings,
    )

    print_section(
        "数据与评论证据：",
        response.evidence,
    )

    print_section(
        "分析限制：",
        response.limitations,
    )

    print_section(
        "建议的下一步：",
        response.next_steps,
    )

    print(
        f"\n回答状态："
        f"{response.answer_status}"
    )


def print_tool_activity(
    tool_calls: list[dict[str, Any]],
) -> None:
    """显示本轮Agent实际调用的工具和参数。"""

    if not tool_calls:
        print(
            "\n本轮未调用业务工具。"
        )
        return

    print("\n本轮业务工具调用：")

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
        print(
            f"   参数：{arguments}"
        )


def print_welcome(
    session_id: str,
    tool_names: list[str],
) -> None:
    """显示程序启动信息和可用命令。"""

    print(
        "\n========================================"
    )
    print("Olist 电商履约诊断 Agent")
    print(
        "========================================"
    )
    print(f"当前session_id：{session_id}")
    print(
        "相同session_id会继续使用Redis中的"
        "历史对话。"
    )

    print("\n当前业务能力：")
    print("1. 查询总体履约指标")
    print("2. 查询业务分组排名")
    print("3. 检索真实顾客评论案例")

    print(
        "\n已加载工具："
        + "、".join(tool_names)
    )

    print("\n可用命令：")
    print("  /help   查看帮助")
    print("  /multi  输入多行复杂问题")
    print("  /clear  清空当前会话历史")
    print("  /exit   退出程序")

    print(
        "\n可以开始提问。"
    )


def print_help() -> None:
    """显示问题示例与控制命令。"""

    print("\n问题示例：")
    print(
        "- 2018年上半年的订单迟到率是多少？"
    )
    print(
        "- 哪些顾客州的迟到率最高？"
    )
    print(
        "- 找5条与商品破损有关的真实评论。"
    )
    print(
        "- 分析整体迟到率、问题最严重的州，"
        "并结合顾客评论给出诊断。"
    )
    print(
        "- 后续分析默认使用2018年上半年。"
    )
    print(
        "- 那只看SP州呢？"
    )
    print(
        "  /multi  输入多行复杂问题，"
        "使用/send发送"
    )


    print("\n控制命令：")
    print("  /help   查看帮助")
    print("  /clear  清空当前会话历史")
    print("  /exit   退出程序")


async def ask_agent(
    memory_chain,
    question: str,
    session_id: str,
) -> dict[str, Any]:
    """调用Agent，并在等待期间显示Thinking动画。"""

    stop_event = asyncio.Event()

    thinking_task = asyncio.create_task(
        show_thinking(stop_event)
    )

    try:
        return await answer_question(
            memory_chain=memory_chain,
            question=question,
            session_id=session_id,
        )
    finally:
        stop_event.set()
        await thinking_task

def read_user_input() -> str:
    """读取单行问题或多行问题。

    普通问题直接输入一行；
    输入/multi后进入多行模式，
    最后单独输入/send进行发送。
    """

    first_line = input(
        "\n你："
    ).strip()

    if first_line.lower() != "/multi":
        return first_line

    print(
        "\n已进入多行输入模式。"
    )
    print(
        "请输入完整问题，最后单独输入 /send 发送。"
    )
    print(
        "输入 /cancel 可以取消本次输入。\n"
    )

    lines: list[str] = []

    while True:
        line = input("... ")

        normalized_line = (
            line.strip().lower()
        )

        if normalized_line == "/send":
            return "\n".join(
                lines
            ).strip()

        if normalized_line == "/cancel":
            print(
                "已取消本次多行输入。"
            )
            return ""

        lines.append(line)

async def conversation_loop(
    memory_chain,
    session_id: str,
) -> None:
    """持续接收用户输入并调用Agent。"""

    while True:
        try:
            question = read_user_input()
        except EOFError:
            print("\n对话已结束。")
            return
        except KeyboardInterrupt:
            print("\n对话已结束。")
            return

        if not question:
            continue

        normalized_command = (
            question.lower()
        )

        if normalized_command in EXIT_COMMANDS:
            print(
                "\nAgent：再见，"
                "本次会话记录已保存在Redis中。"
            )
            return

        if normalized_command == "/help":
            print_help()
            continue

        if normalized_command == "/clear":
            clear_session_history(
                session_id
            )

            print(
                "\nAgent：当前会话历史已经清空。"
            )
            continue

        try:
            result = await ask_agent(
                memory_chain=memory_chain,
                question=question,
                session_id=session_id,
            )

            response = result[
                "structured_response"
            ]

            tool_calls = result[
                "business_tool_calls"
            ]

            print_agent_response(
                response
            )

            print_tool_activity(
                tool_calls
            )

        except KeyboardInterrupt:
            print(
                "\nAgent：本轮请求已中断。"
            )
        except Exception as exc:
            print(
                "\nAgent：本轮处理失败，"
                "请检查MCP、MySQL、Redis和"
                "Ollama服务是否正常。"
            )
            print(
                f"错误："
                f"{type(exc).__name__}: {exc}"
            )


async def main() -> None:
    """创建总协调Agent并进入正式对话循环。"""

    arguments = parse_arguments()

    session_id = (
        arguments.session_id.strip()
    )

    if not session_id:
        raise ValueError(
            "session_id不能为空。"
        )

    print(
        "正在初始化履约诊断Agent..."
    )

    memory_chain, tools = (
        await create_fulfillment_coordinator()
    )

    tool_names = [
        tool.name
        for tool in tools
    ]

    print_welcome(
        session_id=session_id,
        tool_names=tool_names,
    )

    await conversation_loop(
        memory_chain=memory_chain,
        session_id=session_id,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已退出。")
    except Exception as exc:
        print(
            "\n[FAIL] Agent启动失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc