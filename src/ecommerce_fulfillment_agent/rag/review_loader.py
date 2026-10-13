"""从 MySQL 加载真实评论并转换为 LangChain 父文档。

该 Loader 只负责读取、规范化和构建 Document，
不执行文本切块、不生成向量，也不写入 Redis。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from langchain_core.document_loaders.base import BaseLoader
from langchain_core.documents import Document

from ecommerce_fulfillment_agent.infrastructure.mysql_client import (
    mysql_read_connection,
)


def normalize_review_text(text: str | None) -> str:
    """统一空格、换行和制表符，生成稳定的 Embedding 输入文本。"""

    if text is None:
        return ""

    return " ".join(text.split())


def build_content_hash(text: str) -> str:
    """为规范化文本生成稳定哈希，用于识别重复评论内容。"""

    normalized_for_hash = text.casefold()

    return sha256(
        normalized_for_hash.encode("utf-8")
    ).hexdigest()


def serialize_metadata_value(value: Any) -> Any:
    """将数据库类型转换为适合 LangChain metadata 的基础类型。"""

    if isinstance(value, datetime):
        return value.isoformat(sep=" ")

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return value


class MySQLReviewLoader(BaseLoader):
    """分批读取 serving_review_corpus 并生成父 Document。

    使用 review_row_id 进行游标式分页，不使用 OFFSET。
    这样数据量增大后，MySQL 不需要反复跳过前面的记录。
    """

    def __init__(self, batch_size: int = 500) -> None:
        """创建 Loader，并设置每次从 MySQL 读取的记录数。"""

        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0。")

        self.batch_size = batch_size

        # 下面这些统计值会在每次 lazy_load() 开始时重置。
        self.source_rows_read = 0
        self.documents_yielded = 0
        self.whitespace_only_rows_skipped = 0
        self.batches_read = 0

    def lazy_load(self) -> Iterator[Document]:
        """分批读取数据库，并逐条生成 LangChain Document。"""

        self.source_rows_read = 0
        self.documents_yielded = 0
        self.whitespace_only_rows_skipped = 0
        self.batches_read = 0

        last_review_row_id = 0

        with mysql_read_connection() as connection:
            while True:
                cursor = connection.cursor(dictionary=True)

                try:
                    cursor.execute(
                        """
                        SELECT
                            document_id,
                            review_row_id,
                            review_id,
                            order_id,
                            review_score,
                            document_text,
                            review_creation_date,
                            review_answer_timestamp,
                            is_selected_order_review,
                            order_review_row_count,
                            order_status,
                            order_purchase_timestamp,
                            customer_unique_id,
                            customer_city,
                            customer_state,
                            is_delivery_metric_eligible,
                            is_late_delivery,
                            delivery_days,
                            actual_minus_estimated_days,
                            has_timeline_anomaly,
                            item_record_count,
                            distinct_product_count,
                            distinct_seller_count,
                            total_item_value,
                            total_freight_value,
                            total_order_value,
                            total_payment_value
                        FROM serving_review_corpus
                        WHERE review_row_id > %s
                        ORDER BY review_row_id
                        LIMIT %s
                        """,
                        (
                            last_review_row_id,
                            self.batch_size,
                        ),
                    )

                    # 每次最多只把一个批次读入内存。
                    rows = cursor.fetchall()

                finally:
                    cursor.close()

                if not rows:
                    break

                self.batches_read += 1
                self.source_rows_read += len(rows)

                for row in rows:
                    # 无论该行是否能生成文档，都要推进分页游标。
                    last_review_row_id = int(
                        row["review_row_id"]
                    )

                    document = self._build_document(row)

                    if document is None:
                        self.whitespace_only_rows_skipped += 1
                        continue

                    self.documents_yielded += 1
                    yield document

                if len(rows) < self.batch_size:
                    break

    def _build_document(
        self,
        row: dict[str, Any],
    ) -> Document | None:
        """把一行 MySQL 查询结果转换为一个父 Document。"""

        normalized_text = normalize_review_text(
            row["document_text"]
        )

        # 空格、换行或制表符组成的评论没有可检索语义。
        if not normalized_text:
            return None

        metadata_values = {
            "source": "mysql:serving_review_corpus",
            "document_id": row["document_id"],
            "review_row_id": row["review_row_id"],
            "review_id": row["review_id"],
            "order_id": row["order_id"],
            "review_score": row["review_score"],
            "review_creation_date": row[
                "review_creation_date"
            ],
            "review_answer_timestamp": row[
                "review_answer_timestamp"
            ],
            "is_selected_order_review": row[
                "is_selected_order_review"
            ],
            "order_review_row_count": row[
                "order_review_row_count"
            ],
            "order_status": row["order_status"],
            "order_purchase_timestamp": row[
                "order_purchase_timestamp"
            ],
            "customer_unique_id": row[
                "customer_unique_id"
            ],
            "customer_city": row["customer_city"],
            "customer_state": row["customer_state"],
            "is_delivery_metric_eligible": row[
                "is_delivery_metric_eligible"
            ],
            "is_late_delivery": row[
                "is_late_delivery"
            ],
            "delivery_days": row["delivery_days"],
            "actual_minus_estimated_days": row[
                "actual_minus_estimated_days"
            ],
            "has_timeline_anomaly": row[
                "has_timeline_anomaly"
            ],
            "item_record_count": row[
                "item_record_count"
            ],
            "distinct_product_count": row[
                "distinct_product_count"
            ],
            "distinct_seller_count": row[
                "distinct_seller_count"
            ],
            "total_item_value": row[
                "total_item_value"
            ],
            "total_freight_value": row[
                "total_freight_value"
            ],
            "total_order_value": row[
                "total_order_value"
            ],
            "total_payment_value": row[
                "total_payment_value"
            ],
            "content_hash": build_content_hash(
                normalized_text
            ),
            "parent_character_count": len(
                normalized_text
            ),
        }

        metadata = {
            key: serialize_metadata_value(value)
            for key, value in metadata_values.items()
        }

        return Document(
            page_content=normalized_text,
            metadata=metadata,
        )


def main() -> int:
    """扫描全部评论，验证自定义 Loader 的加载结果。"""

    print("模式：只读 MySQL 评论 Loader 验证")
    print("来源表：serving_review_corpus")
    print("批次大小：500")

    loader = MySQLReviewLoader(batch_size=500)
    sample_documents: list[Document] = []

    try:
        # 使用 lazy_load() 逐条消费，不把全部 Document 放进列表。
        for document in loader.lazy_load():
            if len(sample_documents) < 3:
                sample_documents.append(document)

    except Exception as error:
        print(f"\n[FAIL] 评论 Loader 执行失败：{error}")
        return 1

    print("\n=== 1. 加载统计 ===")
    print(f"MySQL来源行数：{loader.source_rows_read:,}")
    print(f"生成父文档数：{loader.documents_yielded:,}")
    print(
        "跳过纯空白记录："
        f"{loader.whitespace_only_rows_skipped:,}"
    )
    print(f"读取批次数：{loader.batches_read:,}")

    expected_document_count = (
        loader.source_rows_read
        - loader.whitespace_only_rows_skipped
    )

    violation_count = 0

    if loader.documents_yielded != expected_document_count:
        print(
            "[FAIL] 生成文档数不等于来源行数减去"
            "纯空白记录数。"
        )
        violation_count += 1
    else:
        print("[PASS] 来源记录与父文档数量关系正确。")

    print("\n=== 2. 父文档示例 ===")

    for document in sample_documents:
        metadata = document.metadata
        preview = document.page_content[:120]

        if len(document.page_content) > 120:
            preview += "..."

        print(
            "\n"
            f"document_id：{metadata['document_id']}\n"
            f"order_id：{metadata['order_id']}\n"
            f"review_score：{metadata['review_score']}\n"
            f"customer_state：{metadata['customer_state']}\n"
            f"is_late_delivery："
            f"{metadata['is_late_delivery']}\n"
            f"content_hash："
            f"{metadata['content_hash'][:16]}...\n"
            f"文本：{preview}"
        )

    for document in sample_documents:
        if not document.page_content.strip():
            violation_count += 1

        if not document.metadata.get("document_id"):
            violation_count += 1

        if not document.metadata.get("content_hash"):
            violation_count += 1

    if violation_count > 0:
        print(
            f"\n[FAIL] Loader 验证发现 "
            f"{violation_count} 个问题。"
        )
        return 1

    print(
        "\n[PASS] MySQL评论已成功转换为"
        "LangChain父Document。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())