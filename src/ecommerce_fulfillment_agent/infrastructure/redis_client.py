"""创建并验证项目使用的 Redis 客户端。"""

from __future__ import annotations

from typing import Any

from redis import Redis

from ecommerce_fulfillment_agent.config import get_redis_settings


REDIS_CLIENT_NAME = "ecommerce-fulfillment-agent"


def create_redis_client() -> Redis:
    """根据项目配置创建可复用的 Redis 客户端。"""

    settings = get_redis_settings()

    return Redis(
        host=settings.host,
        port=settings.port,
        db=settings.database,
        password=settings.password,

        # 后续向量会以二进制形式存储，不能自动按 UTF-8 解码。
        decode_responses=False,

        # 连接 Redis 最多等待 5 秒。
        socket_connect_timeout=5,

        # Redis 命令响应最多等待 5 秒。
        socket_timeout=5,

        # 连接空闲后再次使用时，自动检查连接是否仍然有效。
        health_check_interval=30,

        # 方便以后在 Redis 连接列表中识别本项目。
        client_name=REDIS_CLIENT_NAME,
    )


def _decode_text(value: Any) -> str:
    """把 Redis 返回的字节转换成便于显示的字符串。"""

    if isinstance(value, bytes):
        return value.decode("utf-8")

    return str(value)


def _get_module_names(client: Redis) -> list[str]:
    """读取 Redis 当前已加载的模块名称。"""

    raw_modules = client.execute_command("MODULE", "LIST")
    module_names: list[str] = []

    for raw_module in raw_modules:
        raw_name = None

        if isinstance(raw_module, dict):
            raw_name = raw_module.get(b"name")
            if raw_name is None:
                raw_name = raw_module.get("name")

        elif isinstance(raw_module, (list, tuple)):
            if len(raw_module) % 2 != 0:
                raise RuntimeError(
                    f"无法解析 Redis 模块信息：{raw_module!r}"
                )

            module_info = dict(
                zip(
                    raw_module[::2],
                    raw_module[1::2],
                    strict=True,
                )
            )

            raw_name = module_info.get(b"name")
            if raw_name is None:
                raw_name = module_info.get("name")

        if raw_name is None:
            raise RuntimeError(
                f"Redis 模块记录中缺少名称：{raw_module!r}"
            )

        module_names.append(_decode_text(raw_name))

    return sorted(module_names, key=str.lower)


def _get_search_index_names(client: Redis) -> list[str]:
    """读取当前 Redis 数据库中的 RediSearch 索引名称。"""

    raw_index_names = client.execute_command("FT._LIST")

    return sorted(
        _decode_text(index_name)
        for index_name in raw_index_names
    )


def main() -> None:
    """执行只读的 Redis 连接及搜索模块验证。"""

    settings = get_redis_settings()

    print("模式：只读 Redis 连接与搜索模块验证")
    print(f"Redis 地址：{settings.host}:{settings.port}")
    print(f"Redis 数据库编号：{settings.database}")
    print(f"客户端名称：{REDIS_CLIENT_NAME}")

    client = create_redis_client()

    try:
        print("\n=== 1. Redis 连接验证 ===")

        ping_result = client.ping()

        if ping_result is not True:
            raise RuntimeError(
                f"Redis PING 返回了意外结果：{ping_result!r}"
            )

        server_info = client.info(section="server")

        print("[PASS] Redis PING 成功。")
        print(
            "Redis Server 版本："
            f"{server_info.get('redis_version', '未知')}"
        )
        print(
            "Redis 运行模式："
            f"{server_info.get('redis_mode', '未知')}"
        )

        print("\n=== 2. Redis Stack 模块验证 ===")

        module_names = _get_module_names(client)

        if not module_names:
            raise RuntimeError("Redis 没有加载任何扩展模块。")

        for module_name in module_names:
            print(f"- {module_name}")

        normalized_module_names = {
            module_name.lower()
            for module_name in module_names
        }

        if "search" not in normalized_module_names:
            raise RuntimeError(
                "没有找到 RediSearch 的 search 模块，"
                "后续无法创建向量索引。"
            )

        print("[PASS] RediSearch 模块已经加载。")

        print("\n=== 3. 现有搜索索引检查 ===")

        index_names = _get_search_index_names(client)

        if index_names:
            print(f"现有索引数量：{len(index_names)}")

            for index_name in index_names:
                print(f"- {index_name}")
        else:
            print("现有索引数量：0")
            print("当前还没有创建评论向量索引，这是正常状态。")

        print(
            "\n[PASS] Python 已成功连接 Redis，"
            "并确认当前 Redis 支持向量搜索。"
        )

    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "\n[FAIL] Redis 验证失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc