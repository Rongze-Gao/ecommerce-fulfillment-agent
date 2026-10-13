"""Profile the validated review corpus before building the RAG index.

This module reads the final review documents from MySQL and measures
their length, duplication, review-score coverage, and business metadata.
It does not modify MySQL or write anything to Redis.
"""

from __future__ import annotations

from collections import Counter
from math import floor
from statistics import mean
from typing import Sequence

from ecommerce_fulfillment_agent.infrastructure.mysql_client import (
    mysql_read_connection,
)


def calculate_percentile(
    sorted_values: Sequence[int],
    percentile: float,
) -> float:
    """Calculate an interpolated percentile without external libraries."""

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
    """Return a percentage while safely handling an empty corpus."""

    if total == 0:
        return 0.0

    return round(part / total * 100, 2)


def format_number(value: int | float) -> str:
    """Format numbers for readable console output."""

    if isinstance(value, float):
        return f"{value:,.2f}"

    return f"{value:,}"


def print_table(
    headers: tuple[str, ...],
    rows: list[tuple[object, ...]],
) -> None:
    """Print a small table without requiring another package."""

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

    header_line = " | ".join(
        headers[index].ljust(column_widths[index])
        for index in range(len(headers))
    )
    separator_line = "-+-".join(
        "-" * width
        for width in column_widths
    )

    print(header_line)
    print(separator_line)

    for row in string_rows:
        print(
            " | ".join(
                row[index].ljust(column_widths[index])
                for index in range(len(headers))
            )
        )


def normalize_text_for_comparison(text: str) -> str:
    """Normalize case and whitespace only for duplicate analysis."""

    return " ".join(text.casefold().split())


def get_length_bucket(character_count: int) -> str:
    """Assign one document to a readable character-length interval."""

    if character_count <= 100:
        return "1-100"
    if character_count <= 250:
        return "101-250"
    if character_count <= 500:
        return "251-500"
    if character_count <= 1_000:
        return "501-1,000"

    return "Above 1,000"


def main() -> int:
    """Read and profile the complete serving review corpus."""

    print("Mode: read-only RAG corpus profiling")
    print("Source table: serving_review_corpus")

    try:
        with mysql_read_connection() as connection:
            cursor = connection.cursor()

            try:
                cursor.execute(
                    """
                    SELECT
                        document_id,
                        review_score,
                        document_text,
                        is_late_delivery,
                        has_timeline_anomaly,
                        customer_state,
                        order_status
                    FROM serving_review_corpus
                    ORDER BY review_row_id
                    """
                )
                records = cursor.fetchall()
            finally:
                cursor.close()

    except Exception as error:
        print(f"\n[FAIL] RAG corpus profiling could not complete: {error}")
        return 1

    if not records:
        print("\n[FAIL] serving_review_corpus contains no documents.")
        return 1

    total_documents = len(records)

    document_ids = [
        record[0]
        for record in records
    ]
    review_scores = [
        record[1]
        for record in records
    ]
    document_texts = [
        record[2]
        for record in records
    ]
    late_delivery_values = [
        record[3]
        for record in records
    ]
    anomaly_values = [
        record[4]
        for record in records
    ]
    customer_states = [
        record[5]
        for record in records
    ]
    order_statuses = [
        record[6]
        for record in records
    ]

    character_lengths = sorted(
        len(text)
        for text in document_texts
    )
    word_lengths = sorted(
        len(text.split())
        for text in document_texts
    )

    normalized_text_counts = Counter(
        normalize_text_for_comparison(text)
        for text in document_texts
    )

    duplicated_text_groups = sum(
        1
        for count in normalized_text_counts.values()
        if count > 1
    )
    additional_duplicate_rows = sum(
        count - 1
        for count in normalized_text_counts.values()
        if count > 1
    )
    maximum_text_repetition = max(normalized_text_counts.values())

    print("\n=== 1. Corpus size and uniqueness ===")
    print("确认最终进入RAG候选集合的文档规模及重复情况。")

    uniqueness_rows = [
        ("Total documents", format_number(total_documents)),
        (
            "Distinct document IDs",
            format_number(len(set(document_ids))),
        ),
        (
            "Distinct normalized texts",
            format_number(len(normalized_text_counts)),
        ),
        (
            "Duplicated text groups",
            format_number(duplicated_text_groups),
        ),
        (
            "Additional duplicate rows",
            format_number(additional_duplicate_rows),
        ),
        (
            "Maximum repetitions of one text",
            format_number(maximum_text_repetition),
        ),
    ]

    print_table(
        ("metric_name", "metric_value"),
        uniqueness_rows,
    )

    print("\n=== 2. Document-length distribution ===")
    print("判断一条评论能否直接作为一个RAG文档，以及是否需要切块。")

    length_rows = [
        (
            "Minimum",
            format_number(min(character_lengths)),
            format_number(min(word_lengths)),
        ),
        (
            "25th percentile",
            format_number(
                calculate_percentile(character_lengths, 0.25)
            ),
            format_number(
                calculate_percentile(word_lengths, 0.25)
            ),
        ),
        (
            "Median",
            format_number(
                calculate_percentile(character_lengths, 0.50)
            ),
            format_number(
                calculate_percentile(word_lengths, 0.50)
            ),
        ),
        (
            "75th percentile",
            format_number(
                calculate_percentile(character_lengths, 0.75)
            ),
            format_number(
                calculate_percentile(word_lengths, 0.75)
            ),
        ),
        (
            "90th percentile",
            format_number(
                calculate_percentile(character_lengths, 0.90)
            ),
            format_number(
                calculate_percentile(word_lengths, 0.90)
            ),
        ),
        (
            "95th percentile",
            format_number(
                calculate_percentile(character_lengths, 0.95)
            ),
            format_number(
                calculate_percentile(word_lengths, 0.95)
            ),
        ),
        (
            "99th percentile",
            format_number(
                calculate_percentile(character_lengths, 0.99)
            ),
            format_number(
                calculate_percentile(word_lengths, 0.99)
            ),
        ),
        (
            "Maximum",
            format_number(max(character_lengths)),
            format_number(max(word_lengths)),
        ),
        (
            "Average",
            format_number(mean(character_lengths)),
            format_number(mean(word_lengths)),
        ),
    ]

    print_table(
        ("statistic", "characters", "whitespace_words"),
        length_rows,
    )

    print("\n=== 3. Character-length buckets ===")
    print("统计不同长度区间的文档数量和占比。")

    bucket_order = (
        "1-100",
        "101-250",
        "251-500",
        "501-1,000",
        "Above 1,000",
    )
    bucket_counts = Counter(
        get_length_bucket(length)
        for length in character_lengths
    )

    bucket_rows = [
        (
            bucket,
            format_number(bucket_counts[bucket]),
            f"{calculate_percentage(bucket_counts[bucket], total_documents):.2f}",
        )
        for bucket in bucket_order
    ]

    print_table(
        ("character_range", "document_count", "percentage"),
        bucket_rows,
    )

    print("\n=== 4. Review-score coverage ===")
    print("确认RAG语料是否同时覆盖正面、中立和负面评论。")

    score_counts = Counter(review_scores)
    score_rows = [
        (
            score,
            format_number(score_counts[score]),
            f"{calculate_percentage(score_counts[score], total_documents):.2f}",
        )
        for score in range(1, 6)
    ]

    print_table(
        ("review_score", "document_count", "percentage"),
        score_rows,
    )

    print("\n=== 5. Fulfillment evidence coverage ===")
    print("确认评论文档中保留了迟到、异常和订单状态等业务证据。")

    late_counts = Counter(late_delivery_values)
    anomaly_document_count = sum(
        value == 1
        for value in anomaly_values
    )

    fulfillment_rows = [
        (
            "Late-delivery documents",
            format_number(late_counts[1]),
        ),
        (
            "On-time delivery documents",
            format_number(late_counts[0]),
        ),
        (
            "Delivery not measurable",
            format_number(late_counts[None]),
        ),
        (
            "Timeline-anomaly documents",
            format_number(anomaly_document_count),
        ),
        (
            "Distinct customer states",
            format_number(len(set(customer_states))),
        ),
        (
            "Distinct order statuses",
            format_number(len(set(order_statuses))),
        ),
    ]

    print_table(
        ("metric_name", "metric_value"),
        fulfillment_rows,
    )

    print("\n=== 6. Order-status distribution ===")
    print("查看不同订单状态在RAG评论语料中的覆盖情况。")

    status_counts = Counter(order_statuses)
    status_rows = [
        (
            status,
            format_number(count),
            f"{calculate_percentage(count, total_documents):.2f}",
        )
        for status, count in sorted(
            status_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    print_table(
        ("order_status", "document_count", "percentage"),
        status_rows,
    )

    print("\n=== 7. Longest document samples ===")
    print("只显示最长的5条文档预览，不修改或导出评论内容。")

    longest_records = sorted(
        records,
        key=lambda record: len(record[2]),
        reverse=True,
    )[:5]

    longest_rows: list[tuple[object, ...]] = []

    for record in longest_records:
        document_id = record[0]
        review_score = record[1]
        document_text = record[2]

        single_line_text = " ".join(document_text.split())
        preview = single_line_text[:100]

        if len(single_line_text) > 100:
            preview += "..."

        longest_rows.append(
            (
                document_id,
                review_score,
                len(document_text),
                len(document_text.split()),
                preview,
            )
        )

    print_table(
        (
            "document_id",
            "score",
            "characters",
            "words",
            "preview",
        ),
        longest_rows,
    )

    print(
        "\n[PASS] RAG corpus profiling completed "
        "without modifying data."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())