"""从项目根目录的 .env 文件加载应用配置。"""

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


if not ENV_FILE.is_file():
    raise FileNotFoundError(
        f"没有找到环境变量文件：{ENV_FILE}"
    )


load_dotenv(
    dotenv_path=ENV_FILE,
    encoding="utf-8",
    override=False,
)


def _required_env(name: str) -> str:
    """读取必需的环境变量；缺失或为空时立即报错。"""

    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"缺少必需的环境变量：{name}"
        )

    return value.strip()


def _required_int_env(name: str) -> int:
    """读取必需的环境变量，并将其转换为整数。"""

    value = _required_env(name)

    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"环境变量 {name} 必须是整数，"
            f"当前值为：{value!r}"
        ) from exc


@dataclass(frozen=True, slots=True)
class MySQLSettings:
    """应用程序连接 MySQL 所需的配置。"""

    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RedisSettings:
    """应用程序连接 Redis Stack 所需的配置。"""

    host: str
    port: int

    # DB 0：保存RAG评论切块、向量和索引。
    database: int

    # DB 1：保存不同session_id对应的对话历史。
    chat_database: int

    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class OllamaSettings:
    """本地 Ollama 服务与 Embedding 模型配置。"""

    base_url: str
    embedding_model: str
    translation_model: str
    chat_model: str


def get_mysql_settings() -> MySQLSettings:
    """从环境变量构建并返回 MySQL 配置。"""

    return MySQLSettings(
        host=_required_env("MYSQL_HOST"),
        port=_required_int_env("MYSQL_HOST_PORT"),
        database=_required_env("MYSQL_DATABASE"),
        user=_required_env("MYSQL_USER"),
        password=_required_env("MYSQL_PASSWORD"),
    )


def get_redis_settings() -> RedisSettings:
    """从环境变量构建并返回 Redis 配置。"""

    database = _required_int_env(
        "REDIS_DATABASE"
    )
    chat_database = _required_int_env(
        "REDIS_CHAT_DATABASE"
    )

    if database < 0:
        raise RuntimeError(
            "环境变量 REDIS_DATABASE 不能小于 0。"
        )

    if chat_database < 0:
        raise RuntimeError(
            "环境变量 REDIS_CHAT_DATABASE 不能小于 0。"
        )

    if database == chat_database:
        raise RuntimeError(
            "REDIS_DATABASE 与 REDIS_CHAT_DATABASE "
            "必须使用不同的Redis逻辑数据库。"
        )

    return RedisSettings(
        host=_required_env("REDIS_HOST"),
        port=_required_int_env(
            "REDIS_HOST_PORT"
        ),
        database=database,
        chat_database=chat_database,
        password=_required_env(
            "REDIS_PASSWORD"
        ),
    )

def get_ollama_settings() -> OllamaSettings:
    """从环境变量构建并验证 Ollama 配置。"""

    return OllamaSettings(
        base_url=_required_env(
            "OLLAMA_BASE_URL"
        ),
        embedding_model=_required_env(
            "OLLAMA_EMBEDDING_MODEL"
        ),
        translation_model=_required_env(
            "OLLAMA_TRANSLATION_MODEL"
        ),
        chat_model=_required_env(
            "OLLAMA_CHAT_MODEL"
        ),
    )

@dataclass(frozen=True, slots=True)
class MCPServerSettings:
    """MCP SSE 服务端的监听配置。"""

    host: str
    port: int

    @property
    def sse_url(self) -> str:
        """返回供 MCP 客户端连接的完整 SSE 地址。"""

        return f"http://{self.host}:{self.port}/sse"


def get_mcp_server_settings() -> MCPServerSettings:
    """从环境变量读取并验证 MCP 服务端配置。"""

    port = _required_int_env("MCP_SERVER_PORT")

    if not 1 <= port <= 65535:
        raise RuntimeError(
            "Environment variable MCP_SERVER_PORT must be between "
            f"1 and 65535, but received: {port}"
        )

    return MCPServerSettings(
        host=_required_env("MCP_SERVER_HOST"),
        port=port,
    )