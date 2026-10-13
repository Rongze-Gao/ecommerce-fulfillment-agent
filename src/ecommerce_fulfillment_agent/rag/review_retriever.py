"""使用 Redis 向量索引检索真实评论证据。"""

from __future__ import annotations

from array import array
from collections import Counter
from dataclasses import dataclass
import math
import sys
import warnings
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field
from redis import Redis
from redis.commands.search.query import Query

from ecommerce_fulfillment_agent.infrastructure.redis_client import (
    create_redis_client,
)
from ecommerce_fulfillment_agent.rag.embeddings import (
    create_ollama_embeddings,
)
from ecommerce_fulfillment_agent.rag.query_translator import (
    QueryTranslator,
    create_query_translator,
)
from ecommerce_fulfillment_agent.rag.review_vector_store import (
    REVIEW_INDEX_NAME,
    VECTOR_DIMENSION,
    VECTOR_FIELD_NAME,
    ReviewVectorStore,
)


DEFAULT_RESULT_COUNT = 5
DEFAULT_SINGLE_QUERY_CANDIDATE_COUNT = 20

# 多查询检索时，每一路先保留较多父评论，供后续融合和文本去重。
DEFAULT_PER_QUERY_RESULT_COUNT = 50
DEFAULT_MULTI_QUERY_CANDIDATE_COUNT = 100

# Ollama模型切换后，首次嵌入或首次KNN查询可能出现临时空结果。
MAX_VECTOR_SEARCH_ATTEMPTS = 2

# BGE-M3正常向量的L2范数接近1，低于该值视为无效向量。
MIN_QUERY_VECTOR_L2_NORM = 1e-6


RETURN_FIELDS = (
    "chunk_id",
    "chunk_text",
    "parent_text",
    "parent_document_id",
    "order_id",
    "customer_state",
    "order_status",
    "review_score",
    "is_late_delivery",
    "has_timeline_anomaly",
    "purchase_timestamp",
    "chunk_index",
    "chunk_count",
    "chunk_start_index",
    "review_id",
    "review_row_id",
    "chunk_content_hash",
    "embedding_model",
    "vector_distance",
)


@dataclass(frozen=True, slots=True)
class MultiQuerySearchResult:
    """一次多查询检索的结果和可观察信息。"""

    detected_language: str
    retrieval_queries: tuple[str, ...]
    per_query_document_counts: tuple[int, ...]
    documents: tuple[Document, ...]
    translation_error: str | None = None


@dataclass(slots=True)
class _FusedCandidate:
    """MaxSim 合并过程中的内部候选记录。"""

    document: Document
    matched_queries: list[str]
    query_ranks: dict[str, int]
    best_similarity_score: float
    best_vector_distance: float
    best_matched_query: str


def _vector_to_float32_bytes(
    vector: list[float],
) -> bytes:
    """验证查询向量并转换成Redis需要的little-endian FLOAT32。"""

    if len(vector) != VECTOR_DIMENSION:
        raise ValueError(
            f"查询向量维度错误："
            f"期望 {VECTOR_DIMENSION}，"
            f"实际 {len(vector)}。"
        )

    values = array("f")

    for position, value in enumerate(vector):
        numeric_value = float(value)

        if not math.isfinite(numeric_value):
            raise ValueError(
                f"查询向量第 {position} 个元素"
                f"不是有限数值：{value!r}"
            )

        values.append(numeric_value)

    l2_norm = math.sqrt(
        math.fsum(
            float(value) * float(value)
            for value in values
        )
    )

    if l2_norm < MIN_QUERY_VECTOR_L2_NORM:
        raise ValueError(
            "查询向量的L2范数过小，"
            f"当前范数为 {l2_norm:.12f}。"
        )

    if sys.byteorder != "little":
        values.byteswap()

    result = values.tobytes()
    expected_bytes = VECTOR_DIMENSION * 4

    if len(result) != expected_bytes:
        raise ValueError(
            f"查询向量字节数错误："
            f"期望 {expected_bytes}，"
            f"实际 {len(result)}。"
        )

    return result


def _optional_text(value: Any) -> str | None:
    """把 Redis 返回值转换成可空字符串。"""

    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _required_text(value: Any, field_name: str) -> str:
    """读取必需文本字段。"""

    text = _optional_text(value)
    if text is None or not text.strip():
        raise ValueError(f"Redis 检索结果缺少必需字段：{field_name}")
    return text


def _optional_integer(value: Any, field_name: str) -> int | None:
    """读取可空整数或 Redis NUMERIC 字段。"""

    if value is None or value == "" or value == b"":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Redis 字段 {field_name!r} 不是有效整数：{value!r}"
        ) from exc


def _required_float(value: Any, field_name: str) -> float:
    """读取必需的有限浮点数。"""

    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Redis 字段 {field_name!r} 不是有效浮点数：{value!r}"
        ) from exc

    if not math.isfinite(numeric_value):
        raise ValueError(
            f"Redis 字段 {field_name!r} 不是有限浮点数：{value!r}"
        )
    return numeric_value


def _redis_result_to_document(redis_document: Any) -> Document:
    """把 Redis 子切块命中转换成完整父评论 Document。"""

    chunk_id = _required_text(
        getattr(redis_document, "chunk_id", None),
        "chunk_id",
    )
    chunk_text = _required_text(
        getattr(redis_document, "chunk_text", None),
        "chunk_text",
    )
    parent_text = _required_text(
        getattr(redis_document, "parent_text", None),
        "parent_text",
    )
    parent_document_id = _required_text(
        getattr(redis_document, "parent_document_id", None),
        "parent_document_id",
    )
    vector_distance = _required_float(
        getattr(redis_document, "vector_distance", None),
        "vector_distance",
    )

    metadata = {
        "redis_key": _required_text(
            getattr(redis_document, "id", None),
            "redis_key",
        ),
        "parent_document_id": parent_document_id,
        "chunk_id": chunk_id,
        "matched_chunk_text": chunk_text,
        "order_id": _required_text(
            getattr(redis_document, "order_id", None),
            "order_id",
        ),
        "customer_state": _required_text(
            getattr(redis_document, "customer_state", None),
            "customer_state",
        ),
        "order_status": _required_text(
            getattr(redis_document, "order_status", None),
            "order_status",
        ),
        "review_score": _optional_integer(
            getattr(redis_document, "review_score", None),
            "review_score",
        ),
        "is_late_delivery": _optional_integer(
            getattr(redis_document, "is_late_delivery", None),
            "is_late_delivery",
        ),
        "has_timeline_anomaly": _optional_integer(
            getattr(redis_document, "has_timeline_anomaly", None),
            "has_timeline_anomaly",
        ),
        "purchase_timestamp": _optional_integer(
            getattr(redis_document, "purchase_timestamp", None),
            "purchase_timestamp",
        ),
        "chunk_index": _optional_integer(
            getattr(redis_document, "chunk_index", None),
            "chunk_index",
        ),
        "chunk_count": _optional_integer(
            getattr(redis_document, "chunk_count", None),
            "chunk_count",
        ),
        "chunk_start_index": _optional_integer(
            getattr(redis_document, "chunk_start_index", None),
            "chunk_start_index",
        ),
        "review_id": _optional_text(
            getattr(redis_document, "review_id", None)
        ),
        "review_row_id": _optional_integer(
            getattr(redis_document, "review_row_id", None),
            "review_row_id",
        ),
        "chunk_content_hash": _optional_text(
            getattr(redis_document, "chunk_content_hash", None)
        ),
        "embedding_model": _optional_text(
            getattr(redis_document, "embedding_model", None)
        ),
        "vector_distance": vector_distance,
        "similarity_score": 1.0 - vector_distance,
    }

    return Document(
        id=parent_document_id,
        page_content=parent_text,
        metadata=metadata,
    )


def _normalize_query(value: str) -> str:
    """清理查询中的连续空白。"""

    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("检索问题不能为空。")
    return normalized


def _unique_queries(values: list[str]) -> tuple[str, ...]:
    """按照原顺序删除完全重复的查询。"""

    unique_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_query(value)
        duplicate_key = normalized.casefold()
        if duplicate_key in seen:
            continue
        seen.add(duplicate_key)
        unique_values.append(normalized)
    return tuple(unique_values)


def _normalized_document_text(document: Document) -> str:
    """生成用于检索结果去重的规范化父评论文本。"""

    return " ".join(document.page_content.casefold().split())


class RedisReviewRetriever(BaseRetriever):
    """只负责一个查询的 Redis 子切块向量检索。"""

    client: Redis = Field(exclude=True, repr=False)
    embeddings: Embeddings = Field(exclude=True, repr=False)
    k: int = DEFAULT_RESULT_COUNT
    fetch_k: int = DEFAULT_SINGLE_QUERY_CANDIDATE_COUNT

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _get_relevant_documents(
            self,
            query: str,
            *,
            run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        """执行查询向量生成和Redis KNN检索，临时空结果时重试一次。"""

        normalized_query = _normalize_query(query)

        if self.k <= 0:
            raise ValueError("k 必须大于 0。")

        if self.fetch_k < self.k:
            raise ValueError("fetch_k 不能小于 k。")

        redis_documents: list[Any] = []

        for attempt in range(
                1,
                MAX_VECTOR_SEARCH_ATTEMPTS + 1,
        ):
            try:
                # 每次重试都会重新请求Ollama生成查询向量。
                query_vector = self.embeddings.embed_query(
                    normalized_query
                )

                query_vector_bytes = (
                    _vector_to_float32_bytes(
                        query_vector
                    )
                )
            except ValueError as exc:
                if attempt >= MAX_VECTOR_SEARCH_ATTEMPTS:
                    raise

                warnings.warn(
                    (
                        "查询向量验证失败，"
                        "将重新生成一次向量。"
                        f"查询：{normalized_query!r}；"
                        f"原因：{exc}"
                    ),
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue

            redis_query = (
                Query(
                    f"*=>[KNN {self.fetch_k} "
                    f"@{VECTOR_FIELD_NAME} $query_vector "
                    "AS vector_distance]"
                )
                .return_fields(*RETURN_FIELDS)
                .sort_by(
                    "vector_distance",
                    asc=True,
                )
                .paging(
                    0,
                    self.fetch_k,
                )
                .dialect(2)
            )

            search_result = (
                self.client
                .ft(REVIEW_INDEX_NAME)
                .search(
                    redis_query,
                    query_params={
                        "query_vector": (
                            query_vector_bytes
                        )
                    },
                )
            )

            redis_documents = list(
                search_result.docs
            )

            if redis_documents:
                break

            if attempt < MAX_VECTOR_SEARCH_ATTEMPTS:
                warnings.warn(
                    (
                        "Redis KNN本次返回0条候选，"
                        "将重新生成查询向量并重试一次。"
                        f"查询：{normalized_query!r}"
                    ),
                    RuntimeWarning,
                    stacklevel=2,
                )

        relevant_documents: list[Document] = []
        seen_parent_document_ids: set[str] = set()

        for redis_document in redis_documents:
            document = _redis_result_to_document(
                redis_document
            )

            parent_document_id = document.metadata[
                "parent_document_id"
            ]

            if (
                    parent_document_id
                    in seen_parent_document_ids
            ):
                continue

            seen_parent_document_ids.add(
                parent_document_id
            )
            relevant_documents.append(document)

            if len(relevant_documents) >= self.k:
                break

        return relevant_documents


class MultiQueryReviewRetriever(BaseRetriever):
    """翻译、多路检索、MaxSim 合并并去重的高层 Retriever。"""

    single_query_retriever: RedisReviewRetriever = Field(
        exclude=True,
        repr=False,
    )
    translator: QueryTranslator = Field(exclude=True, repr=False)
    k: int = DEFAULT_RESULT_COUNT

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def search(self, query: str) -> MultiQuerySearchResult:
        """执行完整多查询检索，并返回可观察的检索轨迹。"""

        normalized_query = _normalize_query(query)
        if self.k <= 0:
            raise ValueError("k 必须大于 0。")

        detected_language = "unknown"
        translation_error: str | None = None

        try:
            translation = self.translator.translate(normalized_query)
            detected_language = translation.detected_language
            retrieval_queries = _unique_queries(
                [normalized_query, *translation.portuguese_queries]
            )
        except Exception as exc:
            # 翻译失败时保留 bge-m3 的原始跨语言检索能力。
            translation_error = f"{type(exc).__name__}: {exc}"
            retrieval_queries = (normalized_query,)

        ranked_lists: list[list[Document]] = []
        for retrieval_query in retrieval_queries:
            documents = self.single_query_retriever.invoke(retrieval_query)
            ranked_lists.append(documents)

        fused_candidates: dict[str, _FusedCandidate] = {}

        for retrieval_query, documents in zip(
            retrieval_queries,
            ranked_lists,
            strict=True,
        ):
            for rank, document in enumerate(documents, start=1):
                parent_document_id = str(
                    document.metadata["parent_document_id"]
                )
                similarity_score = float(
                    document.metadata["similarity_score"]
                )
                vector_distance = float(
                    document.metadata["vector_distance"]
                )

                candidate = fused_candidates.get(parent_document_id)
                if candidate is None:
                    fused_candidates[parent_document_id] = _FusedCandidate(
                        document=document,
                        matched_queries=[retrieval_query],
                        query_ranks={retrieval_query: rank},
                        best_similarity_score=similarity_score,
                        best_vector_distance=vector_distance,
                        best_matched_query=retrieval_query,
                    )
                    continue

                candidate.matched_queries.append(retrieval_query)
                candidate.query_ranks[retrieval_query] = rank

                # 同一父评论只保留所有等义查询中相似度最高的命中。
                if similarity_score > candidate.best_similarity_score:
                    candidate.document = document
                    candidate.best_similarity_score = similarity_score
                    candidate.best_vector_distance = vector_distance
                    candidate.best_matched_query = retrieval_query

        ordered_candidates = sorted(
            fused_candidates.values(),
            key=lambda candidate: (
                -candidate.best_similarity_score,
                str(candidate.document.metadata["parent_document_id"]),
            ),
        )

        text_counts = Counter(
            _normalized_document_text(candidate.document)
            for candidate in ordered_candidates
        )
        selected_documents: list[Document] = []
        seen_parent_texts: set[str] = set()

        for candidate in ordered_candidates:
            normalized_text = _normalized_document_text(candidate.document)
            if normalized_text in seen_parent_texts:
                continue
            seen_parent_texts.add(normalized_text)

            metadata = dict(candidate.document.metadata)
            metadata.update(
                {
                    "best_similarity_score": (
                        candidate.best_similarity_score
                    ),
                    "best_vector_distance": (
                        candidate.best_vector_distance
                    ),
                    "best_matched_query": candidate.best_matched_query,
                    "matched_queries": list(candidate.matched_queries),
                    "matched_query_count": len(candidate.matched_queries),
                    "query_ranks": dict(candidate.query_ranks),
                    "duplicate_text_candidate_count": text_counts[
                        normalized_text
                    ],
                    "retrieval_queries": list(retrieval_queries),
                }
            )

            selected_documents.append(
                Document(
                    id=candidate.document.id,
                    page_content=candidate.document.page_content,
                    metadata=metadata,
                )
            )
            if len(selected_documents) >= self.k:
                break

        return MultiQuerySearchResult(
            detected_language=detected_language,
            retrieval_queries=retrieval_queries,
            per_query_document_counts=tuple(
                len(documents) for documents in ranked_lists
            ),
            documents=tuple(selected_documents),
            translation_error=translation_error,
        )

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        """实现 LangChain BaseRetriever 的标准同步接口。"""

        return list(self.search(query).documents)


def create_review_retriever(
    client: Redis,
    k: int = DEFAULT_RESULT_COUNT,
    fetch_k: int = DEFAULT_SINGLE_QUERY_CANDIDATE_COUNT,
) -> RedisReviewRetriever:
    """创建保留兼容性的单查询 Retriever。"""

    return RedisReviewRetriever(
        client=client,
        embeddings=create_ollama_embeddings(),
        k=k,
        fetch_k=fetch_k,
        tags=["olist", "review-rag", "redis", "single-query"],
    )


def create_multi_query_review_retriever(
    client: Redis,
    k: int = DEFAULT_RESULT_COUNT,
    per_query_k: int = DEFAULT_PER_QUERY_RESULT_COUNT,
    fetch_k: int = DEFAULT_MULTI_QUERY_CANDIDATE_COUNT,
) -> MultiQueryReviewRetriever:
    """创建供后续 RAG 和 Agent 使用的多查询 Retriever。"""

    if per_query_k < k:
        raise ValueError("per_query_k 不能小于最终结果数量 k。")
    if fetch_k < per_query_k:
        raise ValueError("fetch_k 不能小于 per_query_k。")

    single_query_retriever = create_review_retriever(
        client=client,
        k=per_query_k,
        fetch_k=fetch_k,
    )
    return MultiQueryReviewRetriever(
        single_query_retriever=single_query_retriever,
        translator=create_query_translator(),
        k=k,
        tags=["olist", "review-rag", "redis", "multi-query", "maxsim"],
    )


def _get_index_document_count(client: Redis) -> int:
    """读取当前 Redis 评论向量索引文档数。"""

    raw_count = ReviewVectorStore(client).get_index_info().get("num_docs", 0)
    try:
        return int(float(raw_count))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"无法解析 Redis 索引文档数：{raw_count!r}"
        ) from exc


def _print_search_result(
    question: str,
    result: MultiQuerySearchResult,
) -> None:
    """输出查询扩展、MaxSim 合并和最终父评论证据。"""

    print(f"问题：{question}")
    print(f"检测语言：{result.detected_language}")

    if result.translation_error is not None:
        print(f"[WARN] 翻译失败，已回退原始查询：{result.translation_error}")

    print("\n=== 1. 实际检索查询 ===")
    for position, (query, count) in enumerate(
        zip(
            result.retrieval_queries,
            result.per_query_document_counts,
            strict=True,
        ),
        start=1,
    ):
        print(f"{position}. 候选父评论={count:,} | {query}")
        if count == 0:
            print("   [WARN] 该路查询没有返回候选，需要单独排查。")

    print("\n=== 2. MaxSim 合并并去重后的父评论 ===")
    print(f"最终返回数量：{len(result.documents)}")

    for rank, document in enumerate(result.documents, start=1):
        metadata = document.metadata
        print(
            f"\n{rank}. best_similarity="
            f"{metadata['best_similarity_score']:.6f}, "
            f"best_distance={metadata['best_vector_distance']:.6f}"
        )
        print(f"   parent_document_id：{metadata['parent_document_id']}")
        print(f"   order_id：{metadata['order_id']}")
        print(f"   review_score：{metadata['review_score']}")
        print(f"   customer_state：{metadata['customer_state']}")
        print(f"   is_late_delivery：{metadata['is_late_delivery']}")
        print(f"   最佳命中查询：{metadata['best_matched_query']}")
        print(f"   命中查询数：{metadata['matched_query_count']}")
        print(
            "   相同文本候选父评论数："
            f"{metadata['duplicate_text_candidate_count']}"
        )
        print(f"   命中chunk_id：{metadata['chunk_id']}")
        print(f"   命中子切块：{metadata['matched_chunk_text']}")
        print(f"   完整父评论：{document.page_content}")


def main() -> None:
    """验证原始问题、翻译查询、MaxSim 合并和文本去重。"""

    question = "订单超过预计送达日期才收到，而且包装已经破损。"
    client = create_redis_client()

    try:
        index_count = _get_index_document_count(client)
        retriever = create_multi_query_review_retriever(client)

        print("模式：多查询 Redis 评论证据检索验证")
        print(f"索引名称：{REVIEW_INDEX_NAME}")
        print(f"当前索引切块数：{index_count:,}")
        print(f"最终返回数量 k：{retriever.k}")
        print(
            "每路保留父评论数："
            f"{retriever.single_query_retriever.k}"
        )
        print(
            "每路Redis候选切块数："
            f"{retriever.single_query_retriever.fetch_k}"
        )
        print("候选合并规则：MaxSim（取各路查询中的最高相似度）\n")

        result = retriever.search(question)
        _print_search_result(question, result)

        empty_query_count = sum(
            count == 0
            for count in result.per_query_document_counts
        )

        if empty_query_count:
            raise RuntimeError(
                "多查询检索仍有"
                f"{empty_query_count}路查询返回0条候选。"
            )

        if not result.documents:
            raise RuntimeError("多查询检索没有返回任何父评论。")

        normalized_texts = [
            _normalized_document_text(document)
            for document in result.documents
        ]
        if len(normalized_texts) != len(set(normalized_texts)):
            raise RuntimeError("最终结果仍然包含完全相同的父评论文本。")

        parent_ids = [
            str(document.metadata["parent_document_id"])
            for document in result.documents
        ]
        if len(parent_ids) != len(set(parent_ids)):
            raise RuntimeError("最终结果仍然包含重复父评论ID。")

        print(
            "\n[PASS] 原始查询和葡萄牙语查询已完成多路检索、"
            "MaxSim合并、父评论去重和文本去重。"
        )
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "\n[FAIL] 多查询评论检索失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc
