"""管理评论切块使用的 Redis 向量索引。"""

from __future__ import annotations

from typing import Any

from redis import Redis
from redis.commands.search.field import (
    NumericField,
    TagField,
    TextField,
    VectorField,
)
from redis.commands.search.indexDefinition import (
    IndexDefinition,
    IndexType,
)
from redis.exceptions import ResponseError

from ecommerce_fulfillment_agent.infrastructure.redis_client import (
    create_redis_client,
)


# Redis Search 索引名称。
REVIEW_INDEX_NAME = "idx:ecommerce:review_chunks"

# 只有以这个前缀开头的 Redis Hash 才会进入评论索引。
REVIEW_KEY_PREFIX = "ecommerce:review_chunk:"

# 必须与 bge-m3 实际生成的向量维度一致。
VECTOR_DIMENSION = 1024

VECTOR_FIELD_NAME = "embedding"
VECTOR_ALGORITHM = "FLAT"
VECTOR_DATA_TYPE = "FLOAT32"
VECTOR_DISTANCE_METRIC = "COSINE"


# 用于验证 Redis 中的实际字段类型是否符合项目设计。
EXPECTED_FIELD_TYPES = {
    "chunk_text": "TEXT",
    "parent_document_id": "TAG",
    "order_id": "TAG",
    "customer_state": "TAG",
    "order_status": "TAG",
    "review_score": "NUMERIC",
    "is_late_delivery": "NUMERIC",
    "has_timeline_anomaly": "NUMERIC",
    "purchase_timestamp": "NUMERIC",
    VECTOR_FIELD_NAME: "VECTOR",
}


def _decode_value(value: Any) -> Any:
    """把 Redis 返回的字节值转换为字符串。"""

    if isinstance(value, bytes):
        return value.decode("utf-8")

    return value


def _as_text(value: Any) -> str:
    """把 Redis 返回值统一转换为字符串。"""

    return str(_decode_value(value))


def _as_integer(value: Any, field_name: str) -> int:
    """把 Redis 返回值转换成整数，并提供清晰的错误信息。"""

    try:
        return int(float(_decode_value(value)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"无法把索引字段 {field_name!r} 的值转换为整数："
            f"{value!r}"
        ) from exc


def _pair_collection_to_dict(value: Any) -> dict[str, Any]:
    """把 Redis 返回的键值对列表转换成字典。"""

    if isinstance(value, dict):
        items = value.items()

    elif isinstance(value, (list, tuple)):
        if len(value) % 2 != 0:
            raise RuntimeError(
                f"Redis 返回的键值对数量不是偶数：{value!r}"
            )

        items = zip(
            value[::2],
            value[1::2],
            strict=True,
        )

    else:
        raise RuntimeError(
            f"无法解析 Redis 索引信息：{value!r}"
        )

    return {
        _as_text(key): _decode_value(item_value)
        for key, item_value in items
    }

def _attribute_collection_to_dict(
    value: Any,
) -> dict[str, Any]:
    """
    解析 FT.INFO 返回的字段定义。

    字段定义中既可能包含键值对，
    也可能包含 NOSTEM、SORTABLE 等独立开关。
    """

    if isinstance(value, dict):
        return {
            _as_text(key): _decode_value(item_value)
            for key, item_value in value.items()
        }

    if not isinstance(value, (list, tuple)):
        raise RuntimeError(
            f"无法解析 Redis 索引字段信息：{value!r}"
        )

    standalone_flags = {
        "NOSTEM",
        "SORTABLE",
        "UNF",
        "NOINDEX",
        "CASESENSITIVE",
        "WITHSUFFIXTRIE",
        "INDEXMISSING",
        "INDEXEMPTY",
    }

    result: dict[str, Any] = {}
    position = 0

    while position < len(value):
        key = _as_text(value[position])

        if key.upper() in standalone_flags:
            result[key.upper()] = True
            position += 1
            continue

        if position + 1 >= len(value):
            raise RuntimeError(
                "Redis 索引字段中出现了无法识别的独立参数："
                f"{value[position]!r}"
            )

        result[key] = _decode_value(value[position + 1])
        position += 2

    return result

def _extract_attribute_map(
    index_info: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """从 FT.INFO 结果中提取索引字段定义。"""

    raw_attributes = index_info.get("attributes")

    if not isinstance(raw_attributes, (list, tuple)):
        raise RuntimeError(
            "FT.INFO 没有返回可以识别的 attributes 字段。"
        )

    attributes: dict[str, dict[str, Any]] = {}

    for raw_attribute in raw_attributes:
        attribute = _attribute_collection_to_dict(raw_attribute)

        identifier = attribute.get("identifier")

        if identifier is None:
            raise RuntimeError(
                f"索引字段中缺少 identifier：{raw_attribute!r}"
            )

        attributes[_as_text(identifier)] = attribute

    return attributes


class ReviewVectorStore:
    """封装评论向量索引的创建、验证及后续读写操作。"""

    def __init__(self, client: Redis) -> None:
        """保存已经建立连接的 Redis 客户端。"""

        self.client = client

    def index_exists(self) -> bool:
        """检查评论向量索引是否已经存在。"""

        raw_index_names = self.client.execute_command("FT._LIST")

        index_names = {
            _as_text(index_name)
            for index_name in raw_index_names
        }

        return REVIEW_INDEX_NAME in index_names

    @staticmethod
    def _build_schema() -> list:
        """构建评论向量索引的字段结构。"""

        return [
            # 评论切块正文。
            # 暂时关闭词干化，避免默认语言规则改变葡萄牙语单词。
            TextField(
                "chunk_text",
                no_stem=True,
            ),

            # TAG 用于精确匹配字符串，例如订单号、州和订单状态。
            TagField("parent_document_id"),
            TagField("order_id"),
            TagField("customer_state"),
            TagField("order_status"),

            # NUMERIC 用于数值过滤和范围过滤。
            NumericField("review_score"),
            NumericField("is_late_delivery"),
            NumericField("has_timeline_anomaly"),
            NumericField("purchase_timestamp"),

            # 评论切块对应的 1024 维向量。
            VectorField(
                VECTOR_FIELD_NAME,
                VECTOR_ALGORITHM,
                {
                    "TYPE": VECTOR_DATA_TYPE,
                    "DIM": VECTOR_DIMENSION,
                    "DISTANCE_METRIC": VECTOR_DISTANCE_METRIC,
                },
            ),
        ]

    def ensure_index(self) -> bool:
        """
        确保评论索引存在。

        返回 True 表示本次新建了索引；
        返回 False 表示索引之前已经存在。
        """

        if self.index_exists():
            return False

        index_definition = IndexDefinition(
            prefix=[REVIEW_KEY_PREFIX],
            index_type=IndexType.HASH,
        )

        try:
            self.client.ft(REVIEW_INDEX_NAME).create_index(
                fields=self._build_schema(),
                definition=index_definition,

                # 评论很短，而且主要是葡萄牙语。
                # 不使用 Redis 默认的英文停用词表。
                stopwords=[],
            )

        except ResponseError as exc:
            # 防止两个进程同时检查后又同时尝试创建索引。
            if "index already exists" in str(exc).lower():
                return False

            raise

        return True

    def get_index_info(self) -> dict[str, Any]:
        """读取 Redis 中评论向量索引的实际信息。"""

        return self.client.ft(REVIEW_INDEX_NAME).info()

    def validate_index(
        self,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """验证实际索引与项目定义是否一致。"""

        index_info = self.get_index_info()
        attributes = _extract_attribute_map(index_info)

        # 检查所有业务字段是否存在，并验证字段类型。
        for field_name, expected_type in EXPECTED_FIELD_TYPES.items():
            attribute = attributes.get(field_name)

            if attribute is None:
                raise RuntimeError(
                    f"索引缺少字段：{field_name}"
                )

            actual_type = _as_text(
                attribute.get("type")
            ).upper()

            if actual_type != expected_type:
                raise RuntimeError(
                    f"索引字段 {field_name!r} 类型不正确："
                    f"期望 {expected_type}，实际 {actual_type}。"
                )

        # 单独验证向量字段的关键参数。
        vector_attribute = attributes[VECTOR_FIELD_NAME]

        actual_algorithm = _as_text(
            vector_attribute.get("algorithm")
        ).upper()

        actual_data_type = _as_text(
            vector_attribute.get("data_type")
        ).upper()

        actual_dimension = _as_integer(
            vector_attribute.get("dim"),
            "dim",
        )

        actual_distance_metric = _as_text(
            vector_attribute.get("distance_metric")
        ).upper()

        if actual_algorithm != VECTOR_ALGORITHM:
            raise RuntimeError(
                "向量索引算法不正确："
                f"期望 {VECTOR_ALGORITHM}，"
                f"实际 {actual_algorithm}。"
            )

        if actual_data_type != VECTOR_DATA_TYPE:
            raise RuntimeError(
                "向量数据类型不正确："
                f"期望 {VECTOR_DATA_TYPE}，"
                f"实际 {actual_data_type}。"
            )

        if actual_dimension != VECTOR_DIMENSION:
            raise RuntimeError(
                "向量维度不正确："
                f"期望 {VECTOR_DIMENSION}，"
                f"实际 {actual_dimension}。"
            )

        if actual_distance_metric != VECTOR_DISTANCE_METRIC:
            raise RuntimeError(
                "向量距离算法不正确："
                f"期望 {VECTOR_DISTANCE_METRIC}，"
                f"实际 {actual_distance_metric}。"
            )

        # 检查索引是否只处理指定前缀的 Redis Hash。
        index_definition = _pair_collection_to_dict(
            index_info.get("index_definition")
        )

        actual_key_type = _as_text(
            index_definition.get("key_type")
        ).upper()

        raw_prefixes = index_definition.get("prefixes")

        if not isinstance(raw_prefixes, (list, tuple)):
            raise RuntimeError(
                f"无法解析索引前缀：{raw_prefixes!r}"
            )

        actual_prefixes = [
            _as_text(prefix)
            for prefix in raw_prefixes
        ]

        if actual_key_type != "HASH":
            raise RuntimeError(
                "索引存储类型不正确："
                f"期望 HASH，实际 {actual_key_type}。"
            )

        if actual_prefixes != [REVIEW_KEY_PREFIX]:
            raise RuntimeError(
                "索引 Key 前缀不正确："
                f"期望 {[REVIEW_KEY_PREFIX]!r}，"
                f"实际 {actual_prefixes!r}。"
            )

        return index_info, attributes


def main() -> None:
    """创建并验证空的评论向量索引。"""

    print("模式：创建并验证 Redis 评论向量索引")
    print(f"索引名称：{REVIEW_INDEX_NAME}")
    print(f"Key 前缀：{REVIEW_KEY_PREFIX}")
    print(
        "向量配置："
        f"{VECTOR_ALGORITHM} / "
        f"{VECTOR_DATA_TYPE} / "
        f"{VECTOR_DIMENSION}维 / "
        f"{VECTOR_DISTANCE_METRIC}"
    )

    client = create_redis_client()

    try:
        vector_store = ReviewVectorStore(client)

        print("\n=== 1. 确保索引存在 ===")

        was_created = vector_store.ensure_index()

        if was_created:
            print("[CREATE] 评论向量索引已创建。")
        else:
            print("[SKIP] 评论向量索引已经存在，不重复创建。")

        print("\n=== 2. 验证索引结构 ===")

        index_info, attributes = vector_store.validate_index()

        for field_name in EXPECTED_FIELD_TYPES:
            field_type = _as_text(
                attributes[field_name].get("type")
            ).upper()

            print(f"[PASS] {field_name}: {field_type}")

        document_count = _as_integer(
            index_info.get("num_docs", 0),
            "num_docs",
        )

        print("\n=== 3. 当前索引状态 ===")
        print(f"已索引文档数：{document_count:,}")

        if document_count == 0:
            print(
                "索引当前为空，这是批量写入评论切块之前的正常状态。"
            )
        else:
            print(
                "索引中已经存在文档，本程序不会删除或覆盖它们。"
            )

        print(
            "\n[PASS] Redis 评论向量索引已经存在，"
            "结构与项目配置一致。"
        )

    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "\n[FAIL] 评论向量索引创建或验证失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc