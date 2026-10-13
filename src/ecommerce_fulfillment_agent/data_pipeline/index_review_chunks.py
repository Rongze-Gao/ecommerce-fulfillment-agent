"""把 MySQL 中的真实评论切块、向量化并写入 Redis。"""

from __future__ import annotations

from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from itertools import islice
import math
import sys
from time import perf_counter
from typing import Any

from langchain_core.documents import Document
from redis import Redis

from ecommerce_fulfillment_agent.config import get_ollama_settings
from ecommerce_fulfillment_agent.infrastructure.redis_client import (
    create_redis_client,
)
from ecommerce_fulfillment_agent.rag.chunking import (
    ChunkingStrategy,
    split_review_document,
)
from ecommerce_fulfillment_agent.rag.embeddings import (
    create_ollama_embeddings,
)
from ecommerce_fulfillment_agent.rag.review_loader import (
    MySQLReviewLoader,
)
from ecommerce_fulfillment_agent.rag.review_vector_store import (
    REVIEW_INDEX_NAME,
    REVIEW_KEY_PREFIX,
    VECTOR_DIMENSION,
    ReviewVectorStore,
)


# None：索引 serving_review_corpus 中全部有文本的父评论。
# 调试时可改为较小整数（例如 10 或 500）做抽样试跑。
PARENT_DOCUMENT_LIMIT: int | None = None

# 每次提交给 Ollama 的切块数量。
EMBEDDING_BATCH_SIZE = 32

# 全量运行时，每处理约 500 个切块显示一次进度。
PROGRESS_INTERVAL = 500

# Redis Hash 的业务结构版本。
# 以后数据结构发生不兼容变化时递增版本号，
# 旧记录就会自动重新生成。
RECORD_VERSION = "2"

CHUNKING_STRATEGY = ChunkingStrategy(
    name="recursive_100_20",
    chunk_size=100,
    chunk_overlap=20,
)

RedisHashValue = str | int | float | bytes


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    """完成身份解析和内容签名、等待检查或向量化的切块。"""

    document: Document
    redis_key: str
    chunk_id: str
    parent_document_id: str
    chunk_text: str
    parent_text: str
    chunk_content_hash: str
    parent_content_hash: str
    index_signature: str


@dataclass(frozen=True, slots=True)
class BatchResult:
    """一个切块批次的处理结果。"""

    redis_keys: tuple[str, ...]
    written_count: int
    skipped_count: int


def _metadata(
    metadata: dict[str, Any],
    *field_names: str,
    required: bool = True,
) -> Any:
    """按照候选字段名顺序读取元数据。"""

    for field_name in field_names:
        value = metadata.get(field_name)

        if value is not None and value != "":
            return value

    if required:
        raise ValueError(
            f"评论切块缺少必要元数据，候选字段为：{field_names}"
        )

    return None


def _decode_optional(value: Any) -> str | None:
    """把 Redis 返回的可空值转换为字符串。"""

    if value is None:
        return None

    if isinstance(value, bytes):
        return value.decode("utf-8")

    return str(value)


def _as_int(value: Any, field_name: str) -> int:
    """把字段安全转换为整数。"""

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"字段 {field_name!r} 必须是整数，实际为：{value!r}"
        ) from exc


def _as_flag(
    value: Any,
    field_name: str,
) -> int | None:
    """把可空业务标记转换为 0、1 或 None。"""

    if value is None or value == "":
        return None

    flag = _as_int(value, field_name)

    if flag not in (0, 1):
        raise ValueError(
            f"字段 {field_name!r} 必须是 0、1 或空值，"
            f"实际为：{value!r}"
        )

    return flag


def _as_timestamp(value: Any) -> int:
    """把订单购买时间转换为 Unix 时间戳。"""

    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(
                f"购买时间戳不是有限数值：{value!r}"
            )

        return int(value)

    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, str):
        normalized = value.strip().replace(
            "Z",
            "+00:00",
        )

        if not normalized:
            raise ValueError("订单购买时间不能为空。")

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                f"无法解析订单购买时间：{value!r}"
            ) from exc

    else:
        raise ValueError(
            "订单购买时间的数据类型不受支持："
            f"{type(value).__name__}"
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return int(parsed.timestamp())


def _text_hash(text: str) -> str:
    """计算文本的 SHA-256 内容指纹。"""

    return sha256(text.encode("utf-8")).hexdigest()


def _vector_bytes(
    vector: Sequence[float],
) -> bytes:
    """把向量转换为 Redis VECTOR 需要的 FLOAT32 字节。"""

    if len(vector) != VECTOR_DIMENSION:
        raise ValueError(
            "向量维度不正确："
            f"期望 {VECTOR_DIMENSION}，实际 {len(vector)}。"
        )

    values = array("f")

    for position, value in enumerate(vector):
        numeric_value = float(value)

        if not math.isfinite(numeric_value):
            raise ValueError(
                f"向量第 {position} 个元素不是有限数值："
                f"{value!r}"
            )

        values.append(numeric_value)

    # Redis 向量字段按照 little-endian FLOAT32 保存。
    if sys.byteorder != "little":
        values.byteswap()

    result = values.tobytes()
    expected_bytes = VECTOR_DIMENSION * 4

    if len(result) != expected_bytes:
        raise ValueError(
            "FLOAT32 字节长度错误："
            f"期望 {expected_bytes}，实际 {len(result)}。"
        )

    return result


def _prepare_chunk(
    chunk: Document,
    embedding_model_name: str,
) -> PreparedChunk:
    """解析切块身份，并生成断点续跑签名。"""

    metadata = chunk.metadata

    parent_document_id = str(
        _metadata(
            metadata,
            "parent_document_id",
            "document_id",
        )
    )

    chunk_index = _as_int(
        _metadata(metadata, "chunk_index"),
        "chunk_index",
    )

    chunk_id_value = _metadata(
        metadata,
        "chunk_id",
        required=False,
    )

    if chunk_id_value is None:
        chunk_id_value = getattr(chunk, "id", None)

    if chunk_id_value is None:
        chunk_id = (
            f"{parent_document_id}:chunk:{chunk_index:04d}"
        )
    else:
        chunk_id = str(chunk_id_value)

    chunk_text = chunk.page_content
    parent_text = str(
        _metadata(metadata, "parent_text")
    )

    if not chunk_text or not chunk_text.strip():
        raise ValueError(
            f"评论切块 {chunk_id!r} 的正文为空。"
        )

    if not parent_text.strip():
        raise ValueError(
            f"评论切块 {chunk_id!r} 的父评论正文为空。"
        )

    chunk_hash = _text_hash(chunk_text)
    parent_hash = _text_hash(parent_text)

    # 签名综合考虑：
    # 1. 数据结构版本
    # 2. 子切块内容
    # 3. 完整父评论内容
    # 4. 嵌入模型
    # 5. 向量维度
    # 6. 切块策略
    #
    # 任何一项变化，都需要重新生成向量并写入 Redis。
    signature_source = "\0".join(
        (
            RECORD_VERSION,
            chunk_hash,
            parent_hash,
            embedding_model_name,
            str(VECTOR_DIMENSION),
            CHUNKING_STRATEGY.name,
        )
    )

    return PreparedChunk(
        document=chunk,
        redis_key=f"{REVIEW_KEY_PREFIX}{chunk_id}",
        chunk_id=chunk_id,
        parent_document_id=parent_document_id,
        chunk_text=chunk_text,
        parent_text=parent_text,
        chunk_content_hash=chunk_hash,
        parent_content_hash=parent_hash,
        index_signature=_text_hash(signature_source),
    )


def _build_hash(
    prepared: PreparedChunk,
    vector: Sequence[float],
    embedding_model_name: str,
) -> dict[str, RedisHashValue]:
    """生成一个 Redis Hash 记录。"""

    metadata = prepared.document.metadata

    review_score = _as_int(
        _metadata(metadata, "review_score"),
        "review_score",
    )

    if review_score not in (1, 2, 3, 4, 5):
        raise ValueError(
            f"review_score 超出 1 到 5：{review_score}"
        )

    is_late_delivery = _as_flag(
        _metadata(
            metadata,
            "is_late_delivery",
            required=False,
        ),
        "is_late_delivery",
    )

    has_timeline_anomaly = _as_flag(
        _metadata(
            metadata,
            "has_timeline_anomaly",
        ),
        "has_timeline_anomaly",
    )

    if has_timeline_anomaly is None:
        raise ValueError(
            "has_timeline_anomaly 不能是空值。"
        )

    purchase_time = _metadata(
        metadata,
        "order_purchase_timestamp",
        "purchase_timestamp",
        "purchase_date",
    )

    chunk_index = _as_int(
        _metadata(metadata, "chunk_index"),
        "chunk_index",
    )

    mapping: dict[str, RedisHashValue] = {
        # 断点续跑和版本审计字段
        "record_version": RECORD_VERSION,
        "index_signature": prepared.index_signature,
        "chunk_content_hash": (
            prepared.chunk_content_hash
        ),
        "parent_content_hash": (
            prepared.parent_content_hash
        ),
        "embedding_model": embedding_model_name,
        "vector_dimension": VECTOR_DIMENSION,
        "chunking_strategy": CHUNKING_STRATEGY.name,

        # 父子文档字段
        "chunk_id": prepared.chunk_id,
        "chunk_text": prepared.chunk_text,
        "parent_text": prepared.parent_text,
        "parent_document_id": (
            prepared.parent_document_id
        ),
        "chunk_index": chunk_index,

        # 订单和评论业务字段
        "order_id": str(
            _metadata(metadata, "order_id")
        ),
        "customer_state": str(
            _metadata(metadata, "customer_state")
        ),
        "order_status": str(
            _metadata(metadata, "order_status")
        ),
        "review_score": review_score,
        "has_timeline_anomaly": (
            has_timeline_anomaly
        ),
        "purchase_timestamp": _as_timestamp(
            purchase_time
        ),

        # 真正用于相似度检索的向量
        "embedding": _vector_bytes(vector),
    }

    # NULL 表示该订单无法判断是否迟到。
    # 不能把 NULL 错误地保存为 0。
    if is_late_delivery is not None:
        mapping["is_late_delivery"] = (
            is_late_delivery
        )

    optional_integer_fields = (
        "chunk_count",
        "review_row_id",
        "is_delivery_metric_eligible",
    )

    for field_name in optional_integer_fields:
        value = _metadata(
            metadata,
            field_name,
            required=False,
        )

        if value is not None:
            mapping[field_name] = _as_int(
                value,
                field_name,
            )

    start_index = _metadata(
        metadata,
        "chunk_start_index",
        "start_index",
        required=False,
    )

    if start_index is not None:
        mapping["chunk_start_index"] = _as_int(
            start_index,
            "chunk_start_index",
        )

    review_id = _metadata(
        metadata,
        "review_id",
        required=False,
    )

    if review_id is not None:
        mapping["review_id"] = str(review_id)

    source_hash = _metadata(
        metadata,
        "content_hash",
        required=False,
    )

    if source_hash is not None:
        mapping["source_content_hash"] = str(
            source_hash
        )

    return mapping


def _index_batch(
    client: Redis,
    embedding_model: Any,
    chunks: list[Document],
    embedding_model_name: str,
) -> BatchResult:
    """只向量化和写入新增或发生变化的切块。"""

    if not chunks:
        return BatchResult(
            redis_keys=(),
            written_count=0,
            skipped_count=0,
        )

    prepared_chunks = [
        _prepare_chunk(
            chunk,
            embedding_model_name,
        )
        for chunk in chunks
    ]

    # 第一步只读取轻量的签名字段，
    # 不读取体积很大的 embedding。
    read_pipeline = client.pipeline(
        transaction=False
    )

    for prepared in prepared_chunks:
        read_pipeline.hget(
            prepared.redis_key,
            "index_signature",
        )

    existing_signatures = read_pipeline.execute()

    chunks_to_write = [
        prepared
        for prepared, existing_signature in zip(
            prepared_chunks,
            existing_signatures,
            strict=True,
        )
        if (
            _decode_optional(existing_signature)
            != prepared.index_signature
        )
    ]

    # 只有签名不存在或发生变化的切块，
    # 才会调用 Ollama 生成向量。
    if chunks_to_write:
        vectors = embedding_model.embed_documents(
            [
                prepared.chunk_text
                for prepared in chunks_to_write
            ]
        )

        if len(vectors) != len(chunks_to_write):
            raise RuntimeError(
                "Ollama 返回的向量数与待写入切块数"
                "不一致："
                f"切块={len(chunks_to_write)}，"
                f"向量={len(vectors)}。"
            )

        write_pipeline = client.pipeline(
            transaction=False
        )

        for prepared, vector in zip(
            chunks_to_write,
            vectors,
            strict=True,
        ):
            # is_late_delivery 是可空字段。
            # 先删除旧值，避免来源数据从 0/1 变成 NULL
            # 后，Redis 中仍然残留旧值。
            write_pipeline.hdel(
                prepared.redis_key,
                "is_late_delivery",
            )

            write_pipeline.hset(
                prepared.redis_key,
                mapping=_build_hash(
                    prepared,
                    vector,
                    embedding_model_name,
                ),
            )

        responses = write_pipeline.execute()
        expected_responses = (
            len(chunks_to_write) * 2
        )

        if len(responses) != expected_responses:
            raise RuntimeError(
                "Redis 返回的写入结果数不正确："
                f"期望 {expected_responses}，"
                f"实际 {len(responses)}。"
            )

    return BatchResult(
        redis_keys=tuple(
            prepared.redis_key
            for prepared in prepared_chunks
        ),
        written_count=len(chunks_to_write),
        skipped_count=(
            len(prepared_chunks)
            - len(chunks_to_write)
        ),
    )


def _index_document_count(
    vector_store: ReviewVectorStore,
) -> int:
    """读取 Redis 搜索索引中的文档数。"""

    raw_count = (
        vector_store
        .get_index_info()
        .get("num_docs", 0)
    )

    try:
        return int(float(raw_count))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"无法解析 Redis 索引文档数：{raw_count!r}"
        ) from exc


def _duration(seconds: float) -> str:
    """把秒数格式化为 HH:MM:SS。"""

    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(
        total_seconds,
        3600,
    )
    minutes, remaining_seconds = divmod(
        remainder,
        60,
    )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{remaining_seconds:02d}"
    )


def _print_progress(
    parent_count: int,
    processed_count: int,
    written_count: int,
    skipped_count: int,
    started_at: float,
) -> None:
    """打印低频进度，避免全量任务刷屏。"""

    elapsed = perf_counter() - started_at

    if elapsed > 0:
        rate = processed_count / elapsed
    else:
        rate = 0.0

    print(
        "[PROGRESS] "
        f"父评论={parent_count:,}, "
        f"已处理切块={processed_count:,}, "
        f"本次生成向量={written_count:,}, "
        f"断点跳过={skipped_count:,}, "
        f"速度={rate:.2f}切块/秒, "
        f"耗时={_duration(elapsed)}"
    )


def main() -> None:
    """执行可断点续跑的评论向量索引。"""

    ollama_settings = get_ollama_settings()

    if PARENT_DOCUMENT_LIMIT is None:
        run_mode = "FULL"
    else:
        run_mode = "SMOKE TEST"

    print("模式：MySQL评论切块向量索引")
    print(f"运行模式：{run_mode}")
    print(f"Redis索引：{REVIEW_INDEX_NAME}")
    print(f"记录结构版本：{RECORD_VERSION}")
    print(
        "嵌入模型："
        f"{ollama_settings.embedding_model}"
    )
    print(f"向量维度：{VECTOR_DIMENSION}")
    print(
        "切块策略："
        f"{CHUNKING_STRATEGY.chunk_size}/"
        f"{CHUNKING_STRATEGY.chunk_overlap}"
    )
    print(
        "嵌入批次大小："
        f"{EMBEDDING_BATCH_SIZE}"
    )
    print("断点续跑：已启用")

    if PARENT_DOCUMENT_LIMIT is None:
        print("父评论限制：无，将处理全部评论")
    else:
        print(
            "父评论限制："
            f"{PARENT_DOCUMENT_LIMIT} 条真实评论"
        )

    client = create_redis_client()

    try:
        vector_store = ReviewVectorStore(client)
        vector_store.ensure_index()
        vector_store.validate_index()

        initial_count = _index_document_count(
            vector_store
        )

        print("\n=== 1. 初始状态 ===")
        print(
            "Redis现有索引文档数："
            f"{initial_count:,}"
        )

        print(
            "\n=== 2. 加载、切块、"
            "向量化并写入 ==="
        )

        loader = MySQLReviewLoader()
        embedding_model = (
            create_ollama_embeddings()
        )

        parent_count = 0
        generated_count = 0
        written_count = 0
        skipped_count = 0
        processed_count = 0

        run_keys: set[str] = set()
        buffer: list[Document] = []

        started_at = perf_counter()
        last_reported_count = 0

        parent_iterator = loader.lazy_load()

        if PARENT_DOCUMENT_LIMIT is None:
            selected_parents = parent_iterator
        else:
            selected_parents = islice(
                parent_iterator,
                PARENT_DOCUMENT_LIMIT,
            )

        def process_batch(
            batch: list[Document],
        ) -> None:
            """处理缓冲区中的一个切块批次。"""

            nonlocal written_count
            nonlocal skipped_count
            nonlocal processed_count
            nonlocal last_reported_count

            result = _index_batch(
                client=client,
                embedding_model=embedding_model,
                chunks=batch,
                embedding_model_name=(
                    ollama_settings.embedding_model
                ),
            )

            run_keys.update(result.redis_keys)
            written_count += result.written_count
            skipped_count += result.skipped_count
            processed_count += len(
                result.redis_keys
            )

            if (
                processed_count
                - last_reported_count
                >= PROGRESS_INTERVAL
            ):
                _print_progress(
                    parent_count=parent_count,
                    processed_count=(
                        processed_count
                    ),
                    written_count=written_count,
                    skipped_count=skipped_count,
                    started_at=started_at,
                )

                last_reported_count = (
                    processed_count
                )

        try:
            for parent_document in selected_parents:
                parent_count += 1
                parent_text = (
                    parent_document.page_content
                )

                if (
                    not parent_text
                    or not parent_text.strip()
                ):
                    raise ValueError(
                        "Loader 返回了空的父评论正文。"
                    )

                child_chunks = (
                    split_review_document(
                        parent_document,
                        CHUNKING_STRATEGY,
                    )
                )

                generated_count += len(
                    child_chunks
                )

                for child_chunk in child_chunks:
                    # 子切块用于向量匹配；
                    # 完整父评论用于后续提供给 LLM。
                    child_chunk.metadata[
                        "parent_text"
                    ] = parent_text

                    buffer.append(child_chunk)

                    if (
                        len(buffer)
                        >= EMBEDDING_BATCH_SIZE
                    ):
                        current_batch = buffer[
                            :EMBEDDING_BATCH_SIZE
                        ]

                        del buffer[
                            :EMBEDDING_BATCH_SIZE
                        ]

                        process_batch(
                            current_batch
                        )

            if buffer:
                process_batch(buffer.copy())
                buffer.clear()

        finally:
            close_iterator = getattr(
                parent_iterator,
                "close",
                None,
            )

            if callable(close_iterator):
                close_iterator()

        if (
            processed_count
            > last_reported_count
        ):
            _print_progress(
                parent_count=parent_count,
                processed_count=processed_count,
                written_count=written_count,
                skipped_count=skipped_count,
                started_at=started_at,
            )

        final_count = _index_document_count(
            vector_store
        )

        elapsed = (
            perf_counter() - started_at
        )

        print("\n=== 3. 写入结果 ===")
        print(
            f"处理父评论数：{parent_count:,}"
        )
        print(
            f"生成切块数：{generated_count:,}"
        )
        print(
            "本次生成向量数："
            f"{written_count:,}"
        )
        print(
            "本次写入Redis数："
            f"{written_count:,}"
        )
        print(
            f"断点跳过数：{skipped_count:,}"
        )
        print(
            "本次唯一Redis Key数："
            f"{len(run_keys):,}"
        )
        print(
            "Redis最终索引文档数："
            f"{final_count:,}"
        )
        print(
            f"本次总耗时：{_duration(elapsed)}"
        )

        if parent_count == 0:
            raise RuntimeError(
                "没有从 MySQL 加载到任何父评论。"
            )

        if generated_count == 0:
            raise RuntimeError(
                "真实评论没有生成任何切块。"
            )

        if processed_count != generated_count:
            raise RuntimeError(
                "已处理切块数与生成切块数"
                "不一致。"
            )

        if (
            written_count + skipped_count
            != generated_count
        ):
            raise RuntimeError(
                "写入数、跳过数与生成切块数"
                "不一致。"
            )

        if (
            len(run_keys)
            != generated_count
        ):
            raise RuntimeError(
                "评论切块生成了重复的 "
                "Redis Key。"
            )

        if final_count < len(run_keys):
            raise RuntimeError(
                "Redis 索引文档数小于"
                "本次唯一切块数。"
            )

        # 全量模式下，索引中不能残留已经不属于
        # 当前语料的旧切块。
        if (
            PARENT_DOCUMENT_LIMIT is None
            and final_count != len(run_keys)
        ):
            raise RuntimeError(
                "全量模式下索引数量与本次语料"
                "不一致，可能存在旧切块。"
            )

        print(
            "\n[PASS] 真实评论索引完成，"
            "完整父评论、子切块向量和"
            "断点签名均已保存。"
        )

        if PARENT_DOCUMENT_LIMIT is not None:
            print(
                "当前仍是小规模验证模式，"
                "尚未索引全部评论。"
            )

    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "\n[FAIL] 评论向量索引流程失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc