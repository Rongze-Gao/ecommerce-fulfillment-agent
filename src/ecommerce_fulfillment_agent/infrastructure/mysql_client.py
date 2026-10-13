"""MySQL connection management for read-only Agent tools."""

from collections.abc import Iterator
from contextlib import contextmanager

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from ecommerce_fulfillment_agent.config import get_mysql_settings


@contextmanager
def mysql_read_connection() -> Iterator[MySQLConnectionAbstract]:
    """Provide a MySQL connection inside a read-only transaction."""

    settings = get_mysql_settings()

    connection = mysql.connector.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
        charset="utf8mb4",
        collation="utf8mb4_0900_ai_ci",
        autocommit=False,
        connection_timeout=10,
    )

    try:
        connection.start_transaction(readonly=True)
        yield connection
    finally:
        try:
            if connection.in_transaction:
                connection.rollback()
        finally:
            connection.close()


def check_mysql_connection() -> dict[str, object]:
    """Run a small query and return non-sensitive connection details."""

    query = """
        SELECT
            VERSION() AS mysql_version,
            DATABASE() AS database_name,
            CURRENT_USER() AS authenticated_user,
            CONNECTION_ID() AS connection_id
    """

    with mysql_read_connection() as connection:
        cursor = connection.cursor(
            dictionary=True,
            buffered=True,
        )

        try:
            cursor.execute(query)
            result = cursor.fetchone()
        finally:
            cursor.close()

    if result is None:
        raise RuntimeError(
            "MySQL connection check returned no result."
        )

    return result


if __name__ == "__main__":
    connection_details = check_mysql_connection()

    print("MySQL read-only connection: PASS")

    for name, value in connection_details.items():
        print(f"{name}: {value}")