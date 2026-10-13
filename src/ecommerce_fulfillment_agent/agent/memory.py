"""使用 Redis 持久化保存 Agent 对话历史。"""

from __future__ import annotations

from urllib.parse import quote

from langchain_community.chat_message_histories import (
    RedisChatMessageHistory,
)
from langchain_core.chat_history import (
    BaseChatMessageHistory,
)

from ecommerce_fulfillment_agent.config import (
    RedisSettings,
    get_redis_settings,
)


# 所有Agent对话历史都使用这个Key前缀。
# 实际Key示例：
# ecommerce:chat_history:fulfillment-session-001
CHAT_HISTORY_KEY_PREFIX = (
    "ecommerce:chat_history:"
)

# 第一版不设置过期时间。
# 只要Redis数据卷仍然存在，对话历史就会一直保留。
CHAT_HISTORY_TTL_SECONDS: int | None = None


def build_redis_url(
    settings: RedisSettings,
) -> str:
    """构建供RedisChatMessageHistory使用的连接地址。"""

    # Redis密码可能包含特殊字符，因此需要先进行URL编码。
    encoded_password = quote(
        settings.password,
        safe="",
    )

    return (
        f"redis://:{encoded_password}"
        f"@{settings.host}:{settings.port}"
        f"/{settings.chat_database}"
    )


def get_session_history(
    session_id: str,
) -> BaseChatMessageHistory:
    """根据session_id获取一份Redis对话历史。"""

    normalized_session_id = session_id.strip()

    if not normalized_session_id:
        raise ValueError(
            "session_id不能为空。"
        )

    settings = get_redis_settings()

    return RedisChatMessageHistory(
        session_id=normalized_session_id,
        url=build_redis_url(settings),
        key_prefix=CHAT_HISTORY_KEY_PREFIX,
        ttl=CHAT_HISTORY_TTL_SECONDS,
    )


def clear_session_history(
    session_id: str,
) -> None:
    """删除指定会话的历史消息。"""

    get_session_history(session_id).clear()


def main() -> None:
    """验证Redis对话历史的保存、重新读取和会话隔离。"""

    settings = get_redis_settings()

    session_a = "memory-test-session-a"
    session_b = "memory-test-session-b"

    user_message = (
        "后续分析默认使用2018年1月1日至"
        "2018年6月30日。"
    )
    ai_message = (
        '{"answer_status":"partially_answered",'
        '"summary":"已记录默认时间范围。"}'
    )

    print("模式：Redis持久化对话Memory验证")
    print(
        f"Redis地址："
        f"{settings.host}:{settings.port}"
    )
    print(
        f"RAG向量数据库："
        f"{settings.database}（本文件不读写）"
    )
    print(
        f"对话历史数据库："
        f"{settings.chat_database}"
    )
    print(
        f"Key前缀："
        f"{CHAT_HISTORY_KEY_PREFIX}"
    )

    # 测试使用固定session_id。
    # 每次运行前先清理测试Key，防止重复运行时消息不断累加。
    clear_session_history(session_a)
    clear_session_history(session_b)

    history_a = get_session_history(
        session_a
    )

    history_a.add_user_message(
        user_message
    )
    history_a.add_ai_message(
        ai_message
    )

    # 重新创建一个History对象。
    # 数据仍能被读出，说明消息保存在Redis中，
    # 而不是保存在当前Python对象内。
    reloaded_history_a = (
        get_session_history(session_a)
    )
    history_b = get_session_history(
        session_b
    )

    session_a_messages = (
        reloaded_history_a.messages
    )
    session_b_messages = (
        history_b.messages
    )

    if len(session_a_messages) != 2:
        raise RuntimeError(
            "会话A应当包含2条消息，"
            f"实际为{len(session_a_messages)}条。"
        )

    if len(session_b_messages) != 0:
        raise RuntimeError(
            "会话B应当保持为空，"
            f"实际为{len(session_b_messages)}条。"
        )

    if (
        session_a_messages[0].type
        != "human"
        or session_a_messages[0].content
        != user_message
    ):
        raise RuntimeError(
            "会话A中的HumanMessage不正确。"
        )

    if (
        session_a_messages[1].type
        != "ai"
        or session_a_messages[1].content
        != ai_message
    ):
        raise RuntimeError(
            "会话A中的AIMessage不正确。"
        )

    print("\n=== 1. 会话A重新读取结果 ===")
    print(f"session_id：{session_a}")
    print(
        "Redis Key："
        f"{CHAT_HISTORY_KEY_PREFIX}{session_a}"
    )
    print(
        f"消息数量："
        f"{len(session_a_messages)}"
    )

    for position, message in enumerate(
        session_a_messages,
        start=1,
    ):
        print(
            f"{position}. "
            f"{message.__class__.__name__}："
            f"{message.content}"
        )

    print("\n=== 2. 会话隔离结果 ===")
    print(f"session_id：{session_b}")
    print(
        f"消息数量："
        f"{len(session_b_messages)}"
    )

    print(
        "\n[PASS] 对话历史已写入Redis DB "
        f"{settings.chat_database}，"
        "同一session_id可以重新读取，"
        "不同session_id之间相互隔离。"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "\n[FAIL] Redis对话Memory验证失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc