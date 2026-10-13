"""将 Olist 官方 CSV 分批导入 MySQL raw 表。

运行前需已执行 database/migrations/001_create_raw_tables.sql。
CSV 固定放在项目根目录 data/raw/olist/ 下，文件名与 TABLE_SPECS 一致。
本脚本只负责导入，不创建表、不计算履约指标。
"""

from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error


# 当前文件位于：
# project_root/src/ecommerce_fulfillment_agent/data_pipeline/load_raw.py
# 因此向上三级得到项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[3]

RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw" / "olist"
ENV_FILE = PROJECT_ROOT / ".env"

# 每次向MySQL提交给executemany的记录数量。
# 这不是只导入5000条，而是将全部数据拆成多个5000条的批次。
BATCH_SIZE = 5_000

# 每导入50000条，在控制台输出一次进度。
PROGRESS_INTERVAL = 50_000


@dataclass(frozen=True)
class TableSpec:
    """描述一个CSV文件与一张MySQL原始表之间的映射。"""

    file_name: str
    table_name: str
    columns: tuple[str, ...]
    expected_rows: int


TABLE_SPECS = (
    TableSpec(
        file_name="olist_customers_dataset.csv",
        table_name="raw_customers",
        columns=(
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ),
        expected_rows=99_441,
    ),
    TableSpec(
        file_name="olist_orders_dataset.csv",
        table_name="raw_orders",
        columns=(
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
        expected_rows=99_441,
    ),
    TableSpec(
        file_name="olist_order_items_dataset.csv",
        table_name="raw_order_items",
        columns=(
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ),
        expected_rows=112_650,
    ),
    TableSpec(
        file_name="olist_order_payments_dataset.csv",
        table_name="raw_order_payments",
        columns=(
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        ),
        expected_rows=103_886,
    ),
    TableSpec(
        file_name="olist_order_reviews_dataset.csv",
        table_name="raw_order_reviews",
        columns=(
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ),
        expected_rows=99_224,
    ),
    TableSpec(
        file_name="olist_products_dataset.csv",
        table_name="raw_products",
        columns=(
            "product_id",
            "product_category_name",
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ),
        expected_rows=32_951,
    ),
    TableSpec(
        file_name="olist_sellers_dataset.csv",
        table_name="raw_sellers",
        columns=(
            "seller_id",
            "seller_zip_code_prefix",
            "seller_city",
            "seller_state",
        ),
        expected_rows=3_095,
    ),
    TableSpec(
        file_name="product_category_name_translation.csv",
        table_name="raw_category_translation",
        columns=(
            "product_category_name",
            "product_category_name_english",
        ),
        expected_rows=71,
    ),
    TableSpec(
        file_name="olist_geolocation_dataset.csv",
        table_name="raw_geolocation",
        columns=(
            "geolocation_zip_code_prefix",
            "geolocation_lat",
            "geolocation_lng",
            "geolocation_city",
            "geolocation_state",
        ),
        expected_rows=1_000_163,
    ),
)


# 这些字段需要从CSV文本转换成Python整数。
INTEGER_COLUMNS = {
    "order_item_id",
    "payment_sequential",
    "payment_installments",
    "review_score",
    "product_name_lenght",
    "product_description_lenght",
    "product_photos_qty",
    "product_weight_g",
    "product_length_cm",
    "product_height_cm",
    "product_width_cm",
}

# 金额使用 Decimal，不能产生浮点误差。
DECIMAL_COLUMNS = {
    "price",
    "freight_value",
    "payment_value",
}

# 经纬度转换成Python的 float，对应MySQL的 DOUBLE。
FLOAT_COLUMNS = {
    "geolocation_lat",
    "geolocation_lng",
}

# 这些字段需要转换成Python datetime对象。
DATETIME_COLUMNS = {
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
    "shipping_limit_date",
    "review_creation_date",
    "review_answer_timestamp",
}


def require_environment_variable(name: str) -> str:
    """读取必需的环境变量；缺失时立即停止。"""

    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Required environment variable is missing: {name}"
        )

    return value


def convert_value(column: str, raw_value: str | None) -> object:
    """将CSV中的文本转换成适合MySQL字段的数据类型。"""

    if raw_value is None:
        raise ValueError(
            f"Column {column!r} is missing from a CSV record."
        )

    # CSV空字符串在数据库中保存为NULL，
    # 不把缺失值错误保存成空字符串或数字0。
    if raw_value == "":
        return None

    if column in INTEGER_COLUMNS:
        # 部分商品数字在CSV中可能表现为40.0，
        # Decimal可以在不引入浮点误差的情况下判断其是否为整数。
        decimal_value = Decimal(raw_value)
        integral_value = decimal_value.to_integral_value()

        if decimal_value != integral_value:
            raise ValueError(
                f"Column {column!r} expected an integer "
                f"but received {raw_value!r}."
            )

        return int(integral_value)

    if column in DECIMAL_COLUMNS:
        return Decimal(raw_value)

    if column in FLOAT_COLUMNS:
        return float(raw_value)

    if column in DATETIME_COLUMNS:
        return datetime.fromisoformat(raw_value)

    # ID、城市、州代码、评论等文本保持原值。
    return raw_value


def build_insert_sql(spec: TableSpec) -> str:
    """根据表配置生成参数化INSERT语句。"""

    columns_sql = ", ".join(
        f"`{column}`" for column in spec.columns
    )

    placeholders = ", ".join(
        "%s" for _ in spec.columns
    )

    return (
        f"INSERT INTO `{spec.table_name}` "
        f"({columns_sql}) "
        f"VALUES ({placeholders})"
    )


def get_table_row_count(cursor, table_name: str) -> int:
    """查询指定数据库表当前有多少行。"""

    cursor.execute(
        f"SELECT COUNT(*) FROM `{table_name}`"
    )

    result = cursor.fetchone()

    if result is None:
        raise RuntimeError(
            f"Could not count rows in table {table_name}."
        )

    return int(result[0])


def load_table(connection, cursor, spec: TableSpec) -> None:
    """导入一张表，并在行数正确后提交事务。"""

    csv_path = RAW_DATA_DIR / spec.file_name

    if not csv_path.is_file():
        raise FileNotFoundError(
            f"Raw CSV file not found: {csv_path}"
        )

    existing_rows = get_table_row_count(
        cursor,
        spec.table_name,
    )

    # 如果此前已经完整导入，则安全跳过。
    if existing_rows == spec.expected_rows:
        connection.commit()

        print(
            f"[SKIP] {spec.table_name}: already contains "
            f"{existing_rows:,} expected rows."
        )
        return

    # 既不是空表，也不是完整表，说明可能存在部分数据。
    # 不自动删除，避免误删用户数据。
    if existing_rows != 0:
        connection.rollback()

        raise RuntimeError(
            f"Table {spec.table_name} contains "
            f"{existing_rows:,} rows; expected either 0 "
            f"or {spec.expected_rows:,}. "
            "The loader stopped to avoid mixing or duplicating data."
        )

    print(
        f"[LOAD] {spec.file_name} -> {spec.table_name}"
    )

    insert_sql = build_insert_sql(spec)

    inserted_rows = 0
    next_progress_report = PROGRESS_INTERVAL
    batch: list[tuple[object, ...]] = []

    try:
        with csv_path.open(
            mode="r",
            encoding="utf-8-sig",
            newline="",
        ) as csv_file:
            reader = csv.DictReader(csv_file)

            actual_header = tuple(
                reader.fieldnames or ()
            )

            if actual_header != spec.columns:
                raise ValueError(
                    f"Header mismatch in {spec.file_name}. "
                    f"Expected {spec.columns}, "
                    f"received {actual_header}."
                )

            for record_number, row in enumerate(
                reader,
                start=1,
            ):
                try:
                    values = tuple(
                        convert_value(
                            column,
                            row.get(column),
                        )
                        for column in spec.columns
                    )

                except (ValueError, InvalidOperation) as exc:
                    raise ValueError(
                        f"Invalid value in {spec.file_name}, "
                        f"data record {record_number}: {exc}"
                    ) from exc

                batch.append(values)

                if len(batch) >= BATCH_SIZE:
                    cursor.executemany(
                        insert_sql,
                        batch,
                    )

                    inserted_rows += len(batch)
                    batch.clear()

                    if inserted_rows >= next_progress_report:
                        print(
                            f"       {inserted_rows:,} / "
                            f"{spec.expected_rows:,} rows inserted"
                        )

                        while (
                            inserted_rows
                            >= next_progress_report
                        ):
                            next_progress_report += (
                                PROGRESS_INTERVAL
                            )

            # 插入最后一个不足5000条的批次。
            if batch:
                cursor.executemany(
                    insert_sql,
                    batch,
                )

                inserted_rows += len(batch)

        # 检查从CSV实际读取的记录数量。
        if inserted_rows != spec.expected_rows:
            raise RuntimeError(
                f"CSV row count for {spec.file_name} "
                f"was {inserted_rows:,}; "
                f"expected {spec.expected_rows:,}."
            )

        # 在提交事务之前检查数据库内的记录数量。
        database_rows = get_table_row_count(
            cursor,
            spec.table_name,
        )

        if database_rows != spec.expected_rows:
            raise RuntimeError(
                f"Database row count for "
                f"{spec.table_name} was "
                f"{database_rows:,}; "
                f"expected {spec.expected_rows:,}."
            )

        # 只有所有检查都通过后，才正式提交该表数据。
        connection.commit()

        print(
            f"[PASS] {spec.table_name}: "
            f"{database_rows:,} rows committed."
        )

    except Exception:
        # 当前表发生任何异常，都撤销该表尚未提交的写入。
        connection.rollback()
        raise


def main() -> int:
    """程序入口：连接MySQL并依次导入9张原始表。"""

    if not ENV_FILE.is_file():
        print(
            f"[FAIL] Environment file not found: {ENV_FILE}"
        )
        return 1

    # 将.env中的配置加载到当前Python进程。
    # 不会打印密码，也不会修改.env文件。
    load_dotenv(
        dotenv_path=ENV_FILE,
        encoding="utf-8",
        override=False,
    )

    connection = None
    cursor = None

    try:
        host = require_environment_variable(
            "MYSQL_HOST"
        )

        port = int(
            require_environment_variable(
                "MYSQL_HOST_PORT"
            )
        )

        database = require_environment_variable(
            "MYSQL_DATABASE"
        )

        user = require_environment_variable(
            "MYSQL_USER"
        )

        password = require_environment_variable(
            "MYSQL_PASSWORD"
        )

        # 只打印非敏感连接信息，不打印密码。
        print(f"Raw data directory: {RAW_DATA_DIR}")
        print(
            f"MySQL target: "
            f"{user}@{host}:{port}/{database}"
        )
        print(f"Batch size: {BATCH_SIZE:,}\n")

        connection = mysql.connector.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            charset="utf8mb4",
            use_unicode=True,
            autocommit=False,
            connection_timeout=10,
            raise_on_warnings=True,
        )

        cursor = connection.cursor()

        for spec in TABLE_SPECS:
            load_table(
                connection,
                cursor,
                spec,
            )

        print(
            "\n[PASS] All 9 raw Olist CSV files "
            "were loaded successfully."
        )

        return 0

    except (
        Error,
        OSError,
        ValueError,
        RuntimeError,
        InvalidOperation,
        csv.Error,
    ) as exc:
        print(
            f"\n[FAIL] Raw-data loading failed: {exc}"
        )

        return 1

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())