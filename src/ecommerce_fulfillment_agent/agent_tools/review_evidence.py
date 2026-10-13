"""把评论RAG检索能力封装为LangChain Tool。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.documents import Document
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from ecommerce_fulfillment_agent.infrastructure.redis_client import (
    create_redis_client,
)
from ecommerce_fulfillment_agent.rag.review_retriever import (
    MultiQuerySearchResult,
    create_multi_query_review_retriever,
)
from ecommerce_fulfillment_agent.rag.review_vector_store import (
    REVIEW_INDEX_NAME,
)


class SearchReviewEvidenceInput(BaseModel):
    """评论证据检索工具的输入参数。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    question: str = Field(
        min_length=1,
        description=(
            "需要从真实顾客评论中查找证据的自然语言问题。"
            "可以使用中文、葡萄牙语或其他语言。"
        ),
    )

    k: int = Field(
        default=5,
        ge=1,
        le=10,
        description=(
            "最终返回的去重父评论数量，"
            "最小为1，最大为10，默认返回5条。"
        ),
    )


def _normalize_question(question: str) -> str:
    """清理问题中的连续空白并拒绝空问题。"""

    normalized = " ".join(question.split())

    if not normalized:
        raise ValueError("评论检索问题不能为空。")

    return normalized

def _delivery_timing_label(
    is_late_delivery: Any,
) -> str:
    """把迟到标记转换为方便Agent理解的文字标签。"""

    if is_late_delivery == 1:
        return "late"

    if is_late_delivery == 0:
        return "not_late"

    return "not_measurable"

def _document_to_evidence(
    document: Document,
    rank: int,
) -> dict[str, Any]:
    """把Retriever返回的Document转换为候选评论证据。"""

    metadata = document.metadata

    is_late_delivery = metadata.get(
        "is_late_delivery"
    )

    return {
        "rank": rank,
        "evidence_role": "semantic_candidate",
        "parent_document_id": str(
            metadata["parent_document_id"]
        ),
        "order_id": str(
            metadata["order_id"]
        ),
        "review_score": metadata.get(
            "review_score"
        ),
        "customer_state": metadata.get(
            "customer_state"
        ),
        "order_status": metadata.get(
            "order_status"
        ),
        "is_late_delivery": is_late_delivery,
        "delivery_timing_label": (
            _delivery_timing_label(
                is_late_delivery
            )
        ),
        "has_timeline_anomaly": metadata.get(
            "has_timeline_anomaly"
        ),
        "similarity_score": round(
            float(
                metadata["best_similarity_score"]
            ),
            6,
        ),
        "vector_distance": round(
            float(
                metadata["best_vector_distance"]
            ),
            6,
        ),
        "best_matched_query": str(
            metadata["best_matched_query"]
        ),
        "matched_query_count": int(
            metadata["matched_query_count"]
        ),
        "matched_chunk_id": str(
            metadata["chunk_id"]
        ),
        "matched_chunk_text": str(
            metadata["matched_chunk_text"]
        ),
        "duplicate_text_candidate_count": int(
            metadata[
                "duplicate_text_candidate_count"
            ]
        ),
        "review_text": document.page_content,
    }

def _build_tool_result(
    question: str,
    result: MultiQuerySearchResult,
) -> str:
    """把多查询检索结果转换为稳定的JSON工具输出。"""

    reviews = [
        _document_to_evidence(
            document=document,
            rank=rank,
        )
        for rank, document in enumerate(
            result.documents,
            start=1,
        )
    ]

    query_counts = list(
        result.per_query_document_counts
    )

    empty_query_count = sum(
        count == 0
        for count in query_counts
    )

    translation_succeeded = (
        result.translation_error is None
    )

    if not reviews:
        status = "no_results"
    elif (
        empty_query_count > 0
        or not translation_succeeded
    ):
        status = "partial_success"
    else:
        status = "success"

    payload = {
        "tool_name": "search_review_evidence",
        "status": status,
        "question": question,
        "evidence_role": (
            "candidate_review_evidence"
        ),
        "source": {
            "type": "redis_vector_index",
            "index_name": REVIEW_INDEX_NAME,
            "corpus": (
                "Olist真实顾客评论"
            ),
        },
        "retrieval_strategy": (
            "原始问题与葡萄牙语等义查询分别检索，"
            "使用MaxSim选择最高相似度，"
            "再进行父评论和完全相同文本去重"
        ),
        "retrieval_health": {
            "query_count": len(
                result.retrieval_queries
            ),
            "successful_query_count": sum(
                count > 0
                for count in query_counts
            ),
            "empty_query_count": (
                empty_query_count
            ),
            "all_queries_returned_candidates": (
                empty_query_count == 0
            ),
            "translation_succeeded": (
                translation_succeeded
            ),
        },
        "detected_language": (
            result.detected_language
        ),
        "retrieval_queries": list(
            result.retrieval_queries
        ),
        "per_query_document_counts": (
            query_counts
        ),
        "translation_error": (
            result.translation_error
        ),
        "result_count": len(reviews),
        "reviews": reviews,
        "limitations": [
            (
                "返回结果是与问题语义相近的候选评论，"
                "不是已经证明所有查询条件同时成立的记录。"
            ),
            (
                "对于包含多个条件的复合问题，"
                "单条评论可能只匹配其中一部分条件；"
                "必须逐条核对review_text、"
                "is_late_delivery和其他元数据。"
            ),
            (
                "返回结果不是全部顾客评论的统计样本，"
                "不得根据Top-K评论计算投诉率、"
                "原因占比或总体趋势。"
            ),
            (
                "向量相似度表示语义接近程度，"
                "不代表评论内容证明了业务因果关系。"
            ),
        ],
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
    )


@tool(
    args_schema=SearchReviewEvidenceInput,
)
def search_review_evidence(
    question: str,
    k: int = 5,
) -> str:
    """
    从Olist真实顾客评论语料库中检索与问题语义最相关的候选评论证据。

    适合处理顾客对迟到、商品破损、包装损坏、未收到商品、
    提前送达、商品错误或服务体验等内容的描述。

    工具会在内部完成查询语言识别、葡萄牙语查询扩展、
    BGE-M3向量生成、Redis向量检索、MaxSim合并和评论去重。

    返回的每条评论都是语义候选。对于包含多个条件的问题，
    Agent必须逐条检查评论原文和订单元数据，不能假定每条评论
    同时满足用户问题中的全部条件。

    结果只能作为定性顾客声音案例，不能用于计算投诉比例、
    问题发生率或证明某个因素导致了履约问题。
    """

    normalized_question = _normalize_question(
        question
    )

    client = create_redis_client()

    try:
        retriever = (
            create_multi_query_review_retriever(
                client=client,
                k=k,
            )
        )

        result = retriever.search(
            normalized_question
        )

        return _build_tool_result(
            question=normalized_question,
            result=result,
        )
    finally:
        client.close()


def main() -> None:
    """直接调用Tool，验证其输入Schema和JSON输出。"""

    question = (
        "订单超过预计送达日期才收到，"
        "而且包装已经破损。"
    )

    print("模式：LangChain评论证据Tool直接调用验证")
    print(f"Tool名称：{search_review_evidence.name}")
    print(f"用户问题：{question}")
    print("返回数量k：5")

    raw_result = search_review_evidence.invoke(
        {
            "question": question,
            "k": 5,
        }
    )

    if not isinstance(raw_result, str):
        raise TypeError(
            "评论证据Tool没有返回JSON字符串。"
        )

    parsed_result = json.loads(raw_result)

    print("\n=== Tool JSON输出 ===")
    print(
        json.dumps(
            parsed_result,
            ensure_ascii=False,
            indent=2,
        )
    )

    if parsed_result["status"] != "success":
        raise RuntimeError(
            "评论证据Tool没有成功返回评论。"
        )

    retrieval_health = parsed_result[
        "retrieval_health"
    ]

    if not retrieval_health[
        "all_queries_returned_candidates"
    ]:
        raise RuntimeError(
            "评论证据Tool存在返回0条候选的检索查询。"
        )

    if not retrieval_health[
        "translation_succeeded"
    ]:
        raise RuntimeError(
            "评论证据Tool的查询翻译没有成功。"
        )

    if parsed_result["result_count"] != 5:
        raise RuntimeError(
            "评论证据Tool返回数量不是5条。"
        )

    print(
        "\n[PASS] search_review_evidence已完成"
        "查询翻译、向量检索、MaxSim合并、"
        "父评论去重和JSON结果封装。"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "\n[FAIL] 评论证据Tool验证失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1) from exc