"""One-time SQLite-to-PostgreSQL data import utility.

This utility is intentionally separate from application runtime. It opens the
SQLite source in immutable read-only mode and refuses to import into a
non-empty PostgreSQL destination.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import Base, engine


TABLE_ORDER = (
    "users",
    "generations",
    "daily_suggestions",
    "referrals",
    "support_tickets",
    "payment_orders",
)
UNIQUE_KEYS = (
    ("users", ("telegram_id",)),
    ("users", ("referral_code",)),
    ("daily_suggestions", ("user_id", "suggestion_date")),
    ("referrals", ("inviter_id", "invitee_id")),
)
FOREIGN_KEYS = (
    ("users", "referred_by_user_id", "users", "id"),
    ("generations", "user_id", "users", "id"),
    ("daily_suggestions", "user_id", "users", "id"),
    ("referrals", "inviter_id", "users", "id"),
    ("referrals", "invitee_id", "users", "id"),
    ("support_tickets", "user_id", "users", "id"),
    ("payment_orders", "user_id", "users", "id"),
)


def read_source_rows(db_path: Path) -> dict[str, list[dict[str, object]]]:
    uri = db_path.resolve().as_uri() + "?mode=ro&immutable=1"
    source = sqlite3.connect(uri, uri=True)
    source.row_factory = sqlite3.Row
    try:
        result: dict[str, list[dict[str, object]]] = {}
        for table_name in TABLE_ORDER:
            target_table = Base.metadata.tables[table_name]
            expected_columns = [column.name for column in target_table.columns]
            source_columns = [row["name"] for row in source.execute(f'PRAGMA table_info("{table_name}")')]
            if set(source_columns) != set(expected_columns):
                raise RuntimeError(
                    f"SQLite columns for {table_name} do not match ORM metadata: "
                    f"source={source_columns}, target={expected_columns}"
                )

            rows: list[dict[str, object]] = []
            selected_columns = ", ".join(f'"{column}"' for column in expected_columns)
            for source_row in source.execute(f'SELECT {selected_columns} FROM "{table_name}" ORDER BY id'):
                item = dict(source_row)
                for column in target_table.columns:
                    value = item[column.name]
                    if value is None:
                        continue
                    if isinstance(column.type, DateTime) and isinstance(value, str):
                        item[column.name] = datetime.fromisoformat(value)
                    elif isinstance(column.type, Boolean):
                        item[column.name] = bool(value)
                rows.append(item)
            result[table_name] = rows
        return result
    finally:
        source.close()


async def table_counts(connection) -> dict[str, int]:
    return {
        table_name: int(await connection.scalar(text(f'SELECT COUNT(*) FROM "{table_name}"')))
        for table_name in TABLE_ORDER
    }


async def reset_sequences(connection) -> dict[str, int]:
    values: dict[str, int] = {}
    for table_name in TABLE_ORDER:
        value = await connection.scalar(
            text(
                f"SELECT setval(pg_get_serial_sequence('public.{table_name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM \"{table_name}\"), 1), "
                f"(SELECT COUNT(*) > 0 FROM \"{table_name}\"))"
            )
        )
        values[table_name] = int(value)
    return values


async def validate_destination(source_rows: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    async with engine.connect() as connection:
        destination_counts = await table_counts(connection)
        source_counts = {table_name: len(rows) for table_name, rows in source_rows.items()}
        if source_counts != destination_counts:
            raise RuntimeError(f"Row-count mismatch: source={source_counts}, destination={destination_counts}")

        id_checks: dict[str, dict[str, object]] = {}
        for table_name, rows in source_rows.items():
            destination_ids = set((await connection.execute(text(f'SELECT id FROM "{table_name}"'))).scalars())
            source_ids = {int(row["id"]) for row in rows}
            if source_ids != destination_ids:
                raise RuntimeError(f"Primary-key mismatch for {table_name}")
            id_checks[table_name] = {
                "count": len(destination_ids),
                "min_id": min(destination_ids) if destination_ids else None,
                "max_id": max(destination_ids) if destination_ids else None,
            }

        orphan_checks: dict[str, int] = {}
        for child_table, child_column, parent_table, parent_column in FOREIGN_KEYS:
            count = await connection.scalar(
                text(
                    f'SELECT COUNT(*) FROM "{child_table}" AS child '
                    f'LEFT JOIN "{parent_table}" AS parent '
                    f'ON child."{child_column}" = parent."{parent_column}" '
                    f'WHERE child."{child_column}" IS NOT NULL '
                    f'AND parent."{parent_column}" IS NULL'
                )
            )
            if count:
                raise RuntimeError(f"Foreign-key orphan count for {child_table}.{child_column}: {count}")
            orphan_checks[f"{child_table}.{child_column}"] = int(count)

        unique_checks: dict[str, int] = {}
        for table_name, columns in UNIQUE_KEYS:
            quoted_columns = ", ".join(f'"{column}"' for column in columns)
            duplicate_groups = await connection.scalar(
                text(
                    f'SELECT COUNT(*) FROM ('
                    f'SELECT {quoted_columns}, COUNT(*) FROM "{table_name}" '
                    f'GROUP BY {quoted_columns} HAVING COUNT(*) > 1'
                    f') AS duplicates'
                )
            )
            if duplicate_groups:
                raise RuntimeError(f"Duplicate unique-key groups for {table_name}: {duplicate_groups}")
            unique_checks[f"{table_name}({', '.join(columns)})"] = int(duplicate_groups)

        source_status_counts: dict[str | None, int] = {}
        for row in source_rows["generations"]:
            status = row["status"]
            source_status_counts[status] = source_status_counts.get(status, 0) + 1
        destination_status_counts = {
            row[0]: int(row[1])
            for row in (await connection.execute(text('SELECT status, COUNT(*) FROM "generations" GROUP BY status')))
        }
        if source_status_counts != destination_status_counts:
            raise RuntimeError("Generation status counts do not match source")

        return {
            "source_row_counts": source_counts,
            "destination_row_counts": destination_counts,
            "primary_key_checks": id_checks,
            "foreign_key_orphan_counts": orphan_checks,
            "unique_duplicate_group_counts": unique_checks,
            "generation_status_counts": destination_status_counts,
        }


async def verify_sequence_insert() -> dict[str, int]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            source_user_id = await connection.scalar(text('SELECT MIN(id) FROM "users"'))
            expected_id = int(
                await connection.scalar(text('SELECT COALESCE(MAX(id), 0) + 1 FROM "support_tickets"'))
            )
            inserted_id = await connection.scalar(
                text(
                    'INSERT INTO "support_tickets" '
                    '(user_id, topic, message, status, created_at) '
                    'VALUES (:user_id, :topic, :message, :status, CURRENT_TIMESTAMP) RETURNING id'
                ),
                {
                    "user_id": source_user_id,
                    "topic": "migration sequence check",
                    "message": "temporary migration sequence verification",
                    "status": "open",
                },
            )
            if inserted_id != expected_id:
                raise RuntimeError(f"Sequence test expected id {expected_id}, received {inserted_id}")
        finally:
            await transaction.rollback()

    async with engine.begin() as connection:
        await reset_sequences(connection)

    async with engine.connect() as connection:
        remaining_rows = await connection.scalar(text('SELECT COUNT(*) FROM "support_tickets"'))
        if remaining_rows != 0:
            raise RuntimeError("Sequence verification left an unexpected support-ticket row")

    return {"expected_insert_id": expected_id, "received_insert_id": int(inserted_id)}


async def main() -> None:
    source_path = PROJECT_ROOT / "mavazegar.db"
    source_rows = read_source_rows(source_path)

    async with engine.begin() as connection:
        before_counts = await table_counts(connection)
        if any(before_counts.values()):
            raise RuntimeError(f"PostgreSQL destination is not empty: {before_counts}")

        for table_name in TABLE_ORDER:
            rows = source_rows[table_name]
            if rows:
                await connection.execute(Base.metadata.tables[table_name].insert(), rows)
        sequence_values = await reset_sequences(connection)

    validation = await validate_destination(source_rows)
    sequence_test = await verify_sequence_insert()
    print(
        json.dumps(
            {
                "destination_counts_before_import": before_counts,
                "sequence_set_values": sequence_values,
                "validation": validation,
                "sequence_insert_check": sequence_test,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(engine.dispose())
