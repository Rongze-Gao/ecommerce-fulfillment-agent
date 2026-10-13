"""使用真实评论数据评估 100/20 RAG 切块策略。

该程序只读取 MySQL，不修改数据库、不生成向量、
不连接 Redis，也不调用大模型。
"""

from __future__ import annotations

from math import floor
from statistics import mean
from typing import Sequence

from langchain_core.documents import Document

from ecommerce_fulfillment_agent.infrastructure.mysql_client import (
    mysql_read_connection,
)
from ecommerce_fulfillment_agent.rag.chunking import (
    ChunkingStrategy,
    split_review_document,
)


# 不切块策略仅作为比较基准，不作为最终索引方案。
WHOLE_REVIEW_STRATEGY = ChunkingStrategy(
    name="whole_review",
    chunk_size=None,
    chunk_overlap=0,
)

# 当前项目准备采用的正式候选策略。
RECURSIVE_100_20_STRATEGY = ChunkingStrategy(
    name="recursive_100_20",
    chunk_size=100,
    chunk_overlap=20,
)


def calculate_percentile(
    sorted_values: Sequence[int],
    percentile: float,
) -> float:
    """计算指定分位数，不额外引入 NumPy 依赖。"""

    if not sorted_values:
        return 0.0

    position = (len(sorted_values) - 1) * percentile
    lower_index = floor(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index

    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]

    return lower_value + (upper_value - lower_value) * fraction


def calculate_percentage(part: int, total: int) -> float:
    """安全计算百分比。"""

    if total == 0:
        return 0.0

    return part / total * 100


def print_table(
    headers: tuple[str, ...],
    rows: list[tuple[object, ...]],
) -> None:
    """在控制台打印不依赖第三方库的简单表格。"""

    string_rows = [
        tuple(str(value) for value in row)
        for row in rows
    ]

    column_widths = [
        max(
            len(headers[column_index]),
            *(
                len(row[column_index])
                for row in string_rows
            ),
        )
        for column_index in range(len(headers))
    ]

    print(
        " | ".join(
            headers[index].ljust(column_widths[index])
            for index in range(len(headers))
        )
    )
    print(
        "-+-".join(
            "-" * width
            for width in column_widths
        )
    )

    for row in string_rows:
        print(
            " | ".join(
                row[index].ljust(column_widths[index])
                for index in range(len(headers))
            )
        )


def profile_strategy(
    connection,
    strategy: ChunkingStrategy,
) -> tuple[dict[str, int | float], list[Document] | None]:
    """扫描全部评论并统计一种切块策略的实际结果。"""

    source_document_count = 0
    excluded_whitespace_only_count = 0
    parent_document_count = 0
    parent_character_total = 0
    split_parent_count = 0

    child_chunk_count = 0
    child_character_total = 0
    maximum_chunks_per_parent = 0

    empty_chunk_count = 0
    missing_start_index_count = 0
    broken_parent_link_count = 0
    duplicate_chunk_id_count = 0

    chunk_lengths: list[int] = []
    known_chunk_ids: set[str] = set()
    sample_chunks: list[Document] | None = None

    cursor = connection.cursor(buffered=True)

    try:
        cursor.execute(
            """
            SELECT
                document_id,
                document_text
            FROM serving_review_corpus
            ORDER BY review_row_id
            """
        )

        for document_id, document_text in cursor:
            source_document_count += 1

            # 将连续的空格、换行和制表符统一成单个空格。
            # 这里只规范化将要用于 Embedding 的文本，不修改 MySQL 原始值。
            normalized_document_text = " ".join(
                document_text.split()
            )

            # 只有空白字符的评论没有可检索语义，因此不生成向量。
            if not normalized_document_text:
                excluded_whitespace_only_count += 1
                continue

            parent_document_count += 1
            parent_character_total += len(
                normalized_document_text
            )

            # 这里只放切块所需的最小元数据。
            # 正式索引时会继续加入订单、评分、州等业务字段。
            parent_document = Document(
                page_content=normalized_document_text,
                metadata={
                    "document_id": document_id,
                },
            )

            chunks = split_review_document(
                parent_document=parent_document,
                strategy=strategy,
            )

            current_chunk_count = len(chunks)

            if current_chunk_count > 1:
                split_parent_count += 1

                # 保存第一条真正发生切分的评论，用于控制台示例。
                if sample_chunks is None:
                    sample_chunks = chunks

            maximum_chunks_per_parent = max(
                maximum_chunks_per_parent,
                current_chunk_count,
            )

            for chunk in chunks:
                child_chunk_count += 1

                chunk_length = len(chunk.page_content)
                child_character_total += chunk_length
                chunk_lengths.append(chunk_length)

                if not chunk.page_content.strip():
                    empty_chunk_count += 1

                if chunk.metadata.get("start_index") is None:
                    missing_start_index_count += 1

                if (
                    chunk.metadata.get("parent_document_id")
                    != document_id
                ):
                    broken_parent_link_count += 1

                chunk_id = chunk.metadata.get("chunk_id")

                if not chunk_id:
                    duplicate_chunk_id_count += 1
                elif chunk_id in known_chunk_ids:
                    duplicate_chunk_id_count += 1
                else:
                    known_chunk_ids.add(chunk_id)

    finally:
        cursor.close()

    sorted_chunk_lengths = sorted(chunk_lengths)

    statistics: dict[str, int | float] = {
        "source_document_count": source_document_count,
        "excluded_whitespace_only_count": (
            excluded_whitespace_only_count
        ),
        "parent_document_count": parent_document_count,
        "parent_character_total": parent_character_total,
        "split_parent_count": split_parent_count,
        "split_parent_percentage": calculate_percentage(
            split_parent_count,
            parent_document_count,
        ),
        "child_chunk_count": child_chunk_count,
        "expansion_factor": (
            child_chunk_count / parent_document_count
            if parent_document_count
            else 0.0
        ),
        "average_chunks_per_parent": (
            child_chunk_count / parent_document_count
            if parent_document_count
            else 0.0
        ),
        "maximum_chunks_per_parent": maximum_chunks_per_parent,
        "minimum_chunk_length": min(sorted_chunk_lengths),
        "average_chunk_length": mean(sorted_chunk_lengths),
        "median_chunk_length": calculate_percentile(
            sorted_chunk_lengths,
            0.50,
        ),
        "p95_chunk_length": calculate_percentile(
            sorted_chunk_lengths,
            0.95,
        ),
        "maximum_chunk_length": max(sorted_chunk_lengths),
        "chunks_below_20_characters": sum(
            length < 20
            for length in sorted_chunk_lengths
        ),
        "child_character_total": child_character_total,
        "additional_processed_characters": max(
            child_character_total - parent_character_total,
            0,
        ),
        "empty_chunk_count": empty_chunk_count,
        "missing_start_index_count": missing_start_index_count,
        "broken_parent_link_count": broken_parent_link_count,
        "duplicate_chunk_id_count": duplicate_chunk_id_count,
    }

    return statistics, sample_chunks


def main() -> int:
    """运行完整评论与 100/20 切块策略的对比分析。"""

    print("Mode: read-only chunking profile")
    print("Source table: serving_review_corpus")
    print("Candidate strategy: chunk_size=100, chunk_overlap=20")

    try:
        with mysql_read_connection() as connection:
            whole_statistics, _ = profile_strategy(
                connection=connection,
                strategy=WHOLE_REVIEW_STRATEGY,
            )

            chunked_statistics, sample_chunks = profile_strategy(
                connection=connection,
                strategy=RECURSIVE_100_20_STRATEGY,
            )

    except Exception as error:
        print(f"\n[FAIL] 切块画像执行失败：{error}")
        return 1

    print("\n=== 1. Strategy comparison ===")
    print("比较完整评论与 100/20 切块后的真实数据规模。")

    comparison_rows = [
        (
            "Source database rows",
            f"{int(whole_statistics['source_document_count']):,}",
            f"{int(chunked_statistics['source_document_count']):,}",
        ),
        (
            "Excluded whitespace-only rows",
            (
                f"{int(whole_statistics['excluded_whitespace_only_count']):,}"
            ),
            (
                f"{int(chunked_statistics['excluded_whitespace_only_count']):,}"
            ),
        ),
        (
            "Indexable parent documents",
            f"{int(whole_statistics['parent_document_count']):,}",
            f"{int(chunked_statistics['parent_document_count']):,}",
        ),
        (
            "Child chunks",
            f"{int(whole_statistics['child_chunk_count']):,}",
            f"{int(chunked_statistics['child_chunk_count']):,}",
        ),
        (
            "Expansion factor",
            f"{whole_statistics['expansion_factor']:.2f}",
            f"{chunked_statistics['expansion_factor']:.2f}",
        ),
        (
            "Split parent documents",
            (
                f"{int(whole_statistics['split_parent_count']):,} "
                f"({whole_statistics['split_parent_percentage']:.2f}%)"
            ),
            (
                f"{int(chunked_statistics['split_parent_count']):,} "
                f"({chunked_statistics['split_parent_percentage']:.2f}%)"
            ),
        ),
        (
            "Average chunks per parent",
            f"{whole_statistics['average_chunks_per_parent']:.2f}",
            f"{chunked_statistics['average_chunks_per_parent']:.2f}",
        ),
        (
            "Maximum chunks per parent",
            f"{int(whole_statistics['maximum_chunks_per_parent']):,}",
            f"{int(chunked_statistics['maximum_chunks_per_parent']):,}",
        ),
        (
            "Minimum chunk length",
            f"{int(whole_statistics['minimum_chunk_length']):,}",
            f"{int(chunked_statistics['minimum_chunk_length']):,}",
        ),
        (
            "Median chunk length",
            f"{whole_statistics['median_chunk_length']:.2f}",
            f"{chunked_statistics['median_chunk_length']:.2f}",
        ),
        (
            "95th-percentile chunk length",
            f"{whole_statistics['p95_chunk_length']:.2f}",
            f"{chunked_statistics['p95_chunk_length']:.2f}",
        ),
        (
            "Maximum chunk length",
            f"{int(whole_statistics['maximum_chunk_length']):,}",
            f"{int(chunked_statistics['maximum_chunk_length']):,}",
        ),
        (
            "Chunks below 20 characters",
            (
                f"{int(whole_statistics['chunks_below_20_characters']):,}"
            ),
            (
                f"{int(chunked_statistics['chunks_below_20_characters']):,}"
            ),
        ),
        (
            "Additional processed characters",
            (
                f"{int(whole_statistics['additional_processed_characters']):,}"
            ),
            (
                f"{int(chunked_statistics['additional_processed_characters']):,}"
            ),
        ),
    ]

    print_table(
        (
            "metric_name",
            "whole_review",
            "recursive_100_20",
        ),
        comparison_rows,
    )

    print("\n=== 2. Parent-child integrity ===")
    print("验证切块后是否出现空片段、身份丢失或编号重复。")

    integrity_rows = [
        (
            "Empty chunks",
            int(chunked_statistics["empty_chunk_count"]),
        ),
        (
            "Missing start indexes",
            int(
                chunked_statistics[
                    "missing_start_index_count"
                ]
            ),
        ),
        (
            "Broken parent links",
            int(
                chunked_statistics[
                    "broken_parent_link_count"
                ]
            ),
        ),
        (
            "Duplicate or missing chunk IDs",
            int(
                chunked_statistics[
                    "duplicate_chunk_id_count"
                ]
            ),
        ),
    ]

    print_table(
        ("check_name", "violation_count"),
        integrity_rows,
    )

    if sample_chunks:
        print("\n=== 3. Actual 100/20 split example ===")
        print("展示第一条真正被切分的评论及其子片段。")

        first_chunk = sample_chunks[0]

        print(
            "Parent document: "
            f"{first_chunk.metadata['parent_document_id']}"
        )
        print(
            "Parent characters: "
            f"{first_chunk.metadata['parent_character_count']}"
        )
        print(f"Chunk count: {len(sample_chunks)}")

        for chunk in sample_chunks:
            print(
                "\n"
                f"[{chunk.metadata['chunk_id']}] "
                f"start={chunk.metadata['start_index']}, "
                f"characters={chunk.metadata['chunk_character_count']}"
            )
            print(chunk.page_content)

    violation_count = sum(
        int(chunked_statistics[key])
        for key in (
            "empty_chunk_count",
            "missing_start_index_count",
            "broken_parent_link_count",
            "duplicate_chunk_id_count",
        )
    )

    if violation_count > 0:
        print(
            f"\n[FAIL] 切块结果发现 {violation_count} 个完整性问题。"
        )
        return 1

    print(
        "\n[PASS] 100/20 切块画像完成，"
        "父子文档关系验证通过。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())