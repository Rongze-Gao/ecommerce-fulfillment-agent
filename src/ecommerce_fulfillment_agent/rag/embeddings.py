"""创建并验证项目使用的 Ollama 文本嵌入模型。"""

from __future__ import annotations

import math
from collections.abc import Sequence

from langchain_ollama import OllamaEmbeddings

from ecommerce_fulfillment_agent.config import get_ollama_settings


# 这里只使用少量文本做连通性与向量质量的基础验证。
# 不会读取全部评论，也不会把向量写入 Redis。
TEST_QUERY = "Quais clientes reclamaram de atraso na entrega?"

TEST_DOCUMENTS = [
    "O pedido chegou depois do prazo previsto.",
    "O produto chegou antes do prazo e em perfeito estado.",
    "O produto chegou danificado e a embalagem estava aberta.",
]


def create_ollama_embeddings() -> OllamaEmbeddings:
    """根据项目配置创建可复用的 Ollama 嵌入模型对象。"""

    settings = get_ollama_settings()

    return OllamaEmbeddings(
        model=settings.embedding_model,
        base_url=settings.base_url,
    )


def _vector_norm(vector: Sequence[float]) -> float:
    """计算向量的 L2 范数，用于判断向量是否为有效的非零向量。"""

    return math.sqrt(sum(float(value) ** 2 for value in vector))


def _validate_vector(
    vector_name: str,
    vector: Sequence[float],
    expected_dimension: int | None = None,
) -> None:
    """验证向量非空、维度一致，并且只包含有限数值。"""

    if not vector:
        raise ValueError(f"{vector_name} 是空向量。")

    if expected_dimension is not None and len(vector) != expected_dimension:
        raise ValueError(
            f"{vector_name} 的维度不一致："
            f"期望 {expected_dimension}，实际 {len(vector)}。"
        )

    for position, value in enumerate(vector):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"{vector_name} 的第 {position} 个元素不是数值：{value!r}"
            )

        if not math.isfinite(float(value)):
            raise ValueError(
                f"{vector_name} 的第 {position} 个元素不是有限数值：{value!r}"
            )

    if _vector_norm(vector) == 0:
        raise ValueError(f"{vector_name} 是零向量。")


def _cosine_similarity(
    left_vector: Sequence[float],
    right_vector: Sequence[float],
) -> float:
    """计算两个向量之间的余弦相似度。"""

    if len(left_vector) != len(right_vector):
        raise ValueError(
            "无法计算不同维度向量之间的余弦相似度："
            f"{len(left_vector)} != {len(right_vector)}"
        )

    left_norm = _vector_norm(left_vector)
    right_norm = _vector_norm(right_vector)

    if left_norm == 0 or right_norm == 0:
        raise ValueError("无法计算零向量的余弦相似度。")

    dot_product = sum(
        float(left_value) * float(right_value)
        for left_value, right_value in zip(
            left_vector,
            right_vector,
            strict=True,
        )
    )

    return dot_product / (left_norm * right_norm)


def main() -> None:
    """执行一次小规模嵌入模型验证。"""

    settings = get_ollama_settings()

    print("模式：Ollama 嵌入模型连通性与向量验证")
    print(f"Ollama 地址：{settings.base_url}")
    print(f"嵌入模型：{settings.embedding_model}")
    print(f"测试文档数：{len(TEST_DOCUMENTS)}")

    embeddings = create_ollama_embeddings()

    print("\n=== 1. 生成查询向量 ===")
    query_vector = embeddings.embed_query(TEST_QUERY)

    _validate_vector(
        vector_name="查询向量",
        vector=query_vector,
    )

    vector_dimension = len(query_vector)

    print(f"查询文本：{TEST_QUERY}")
    print(f"向量维度：{vector_dimension}")
    print(f"向量 L2 范数：{_vector_norm(query_vector):.6f}")
    print(
        "前 5 个向量值："
        + ", ".join(
            f"{float(value):.6f}"
            for value in query_vector[:5]
        )
    )

    print("\n=== 2. 生成文档向量 ===")
    document_vectors = embeddings.embed_documents(TEST_DOCUMENTS)

    if len(document_vectors) != len(TEST_DOCUMENTS):
        raise ValueError(
            "返回的文档向量数量不正确："
            f"期望 {len(TEST_DOCUMENTS)}，"
            f"实际 {len(document_vectors)}。"
        )

    for index, document_vector in enumerate(document_vectors, start=1):
        _validate_vector(
            vector_name=f"文档向量 {index}",
            vector=document_vector,
            expected_dimension=vector_dimension,
        )

        print(
            f"文档 {index}：维度={len(document_vector)}, "
            f"L2 范数={_vector_norm(document_vector):.6f}"
        )

    print("\n=== 3. 查询与文档的余弦相似度 ===")

    similarity_results = []

    for index, (document_text, document_vector) in enumerate(
        zip(TEST_DOCUMENTS, document_vectors, strict=True),
        start=1,
    ):
        similarity = _cosine_similarity(
            query_vector,
            document_vector,
        )

        similarity_results.append(
            (index, document_text, similarity)
        )

    similarity_results.sort(
        key=lambda result: result[2],
        reverse=True,
    )

    for rank, (index, document_text, similarity) in enumerate(
        similarity_results,
        start=1,
    ):
        print(
            f"{rank}. 文档 {index} "
            f"| 相似度={similarity:.6f} "
            f"| {document_text}"
        )

    print(
        "\n[PASS] Ollama 嵌入模型连接成功，"
        "查询向量和文档向量均通过基础验证。"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "\n[FAIL] Ollama 嵌入模型验证失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc