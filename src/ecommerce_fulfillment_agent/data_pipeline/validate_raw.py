"""导入前校验 data/raw/olist 下 CSV 是否存在、表头与约定行数是否一致。"""

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw" / "olist"

DATASET_CONTRACTS: dict[str, tuple[int, tuple[str, ...]]] = {
    "olist_customers_dataset.csv": (
        99_441,
        (
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ),
    ),
    "olist_geolocation_dataset.csv": (
        1_000_163,
        (
            "geolocation_zip_code_prefix",
            "geolocation_lat",
            "geolocation_lng",
            "geolocation_city",
            "geolocation_state",
        ),
    ),
    "olist_order_items_dataset.csv": (
        112_650,
        (
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ),
    ),
    "olist_order_payments_dataset.csv": (
        103_886,
        (
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        ),
    ),
    "olist_order_reviews_dataset.csv": (
        99_224,
        (
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ),
    ),
    "olist_orders_dataset.csv": (
        99_441,
        (
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
    ),
    "olist_products_dataset.csv": (
        32_951,
        (
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
    ),
    "olist_sellers_dataset.csv": (
        3_095,
        (
            "seller_id",
            "seller_zip_code_prefix",
            "seller_city",
            "seller_state",
        ),
    ),
    "product_category_name_translation.csv": (
        71,
        (
            "product_category_name",
            "product_category_name_english",
        ),
    ),
}


def inspect_csv(file_path: Path) -> tuple[tuple[str, ...], int]:
    """Read one CSV completely and return its header and data-row count."""

    with file_path.open(
        mode="r",
        encoding="utf-8-sig",
        newline="",
    ) as csv_file:
        reader = csv.reader(csv_file)
        header = next(reader, None)

        if header is None:
            raise ValueError("CSV file is empty")

        row_count = sum(1 for _ in reader)

    return tuple(header), row_count


def validate_csv(
    file_path: Path,
    expected_rows: int,
    expected_header: tuple[str, ...],
) -> bool:
    """Validate the encoding, header, and row count of one CSV file."""

    try:
        actual_header, actual_rows = inspect_csv(file_path)
    except UnicodeDecodeError as error:
        print(f"[FAIL] {file_path.name}")
        print(f"       UTF-8 decoding error: {error}")
        return False
    except (OSError, csv.Error, ValueError) as error:
        print(f"[FAIL] {file_path.name}")
        print(f"       Unable to read CSV: {error}")
        return False

    is_valid = True

    if actual_header != expected_header:
        print(f"[FAIL] {file_path.name}")
        print("       Header does not match the data contract.")
        print(f"       Expected: {expected_header}")
        print(f"       Actual:   {actual_header}")
        is_valid = False

    if actual_rows != expected_rows:
        if is_valid:
            print(f"[FAIL] {file_path.name}")

        print(
            "       Row count mismatch: "
            f"expected {expected_rows:,}, found {actual_rows:,}"
        )
        is_valid = False

    if is_valid:
        print(
            f"[PASS] {file_path.name}: "
            f"{actual_rows:,} rows, "
            f"{len(actual_header)} columns"
        )

    return is_valid


def main() -> None:
    """Validate the complete raw Olist dataset."""

    if not RAW_DATA_DIR.is_dir():
        raise FileNotFoundError(
            f"Raw data directory does not exist: {RAW_DATA_DIR}"
        )

    expected_files = set(DATASET_CONTRACTS)

    actual_files = {
        file_path.name
        for file_path in RAW_DATA_DIR.glob("*.csv")
    }

    missing_files = expected_files - actual_files
    unexpected_files = actual_files - expected_files

    print(f"Raw data directory: {RAW_DATA_DIR}")
    print(f"Expected CSV files: {len(expected_files)}")
    print(f"Found CSV files: {len(actual_files)}")
    print()

    if missing_files:
        print("[FAIL] Missing files:")
        for file_name in sorted(missing_files):
            print(f"       - {file_name}")

    if unexpected_files:
        print("[FAIL] Unexpected CSV files:")
        for file_name in sorted(unexpected_files):
            print(f"       - {file_name}")

    if missing_files or unexpected_files:
        raise SystemExit(1)

    all_files_valid = True

    for file_name, contract in DATASET_CONTRACTS.items():
        expected_rows, expected_header = contract
        file_path = RAW_DATA_DIR / file_name

        file_is_valid = validate_csv(
            file_path=file_path,
            expected_rows=expected_rows,
            expected_header=expected_header,
        )

        if not file_is_valid:
            all_files_valid = False

    if not all_files_valid:
        print("\n[FAIL] Raw-data validation failed.")
        raise SystemExit(1)

    print("\n[PASS] Raw Olist dataset validation completed successfully.")


if __name__ == "__main__":
    main()