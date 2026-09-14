"""Read-only validator for SQLite -> PostgreSQL migration.

This script performs SELECT-only validation. It never writes to SQLite or PostgreSQL.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / "mavazegar.db"

# Import the project's configured PostgreSQL engine and ORM metadata.
import sys
sys.path.insert(0, str(PROJECT_ROOT))
from app.db import Base, engine  # noqa: E402


TABLE_ORDER = (
    "users",
    "generations",
    "daily_suggestions",
    "referrals",
    "support_tickets",
    "payment_orders",
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

UNIQUE_KEYS = (
    ("users", ("telegram_id",)),
    ("users", ("referral_code",)),
    ("daily_suggestions", ("user_id", "suggestion_date")),
    ("referrals", ("inviter_id", "invitee_id")),
)


def canonical(value: Any) -> Any:
    """Normalize SQLite/asyncpg representations for exact value comparison."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def read_sqlite() -> dict[str, list[dict[str, Any]]]:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"SQLite database not found: {SOURCE_PATH}")

    uri = SOURCE_PATH.resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        result: dict[str, list[dict[str, Any]]] = {}
        for table in TABLE_ORDER:
            orm_columns = [c.name for c in Base.metadata.tables[table].columns]
            source_columns = [
                row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')
            ]
            if set(source_columns) != set(orm_columns):
                raise RuntimeError(
                    f"Column mismatch in {table}: "
                    f"SQLite={source_columns}; ORM={orm_columns}"
                )

            rows = []
            selected = ", ".join(f'"{c}"' for c in orm_columns)
            for row in conn.execute(f'SELECT {selected} FROM "{table}" ORDER BY id'):
                rows.append({k: canonical(row[k]) for k in orm_columns})
            result[table] = rows
        return result
    finally:
        conn.close()


async def read_postgres() -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    async with engine.connect() as conn:
        for table in TABLE_ORDER:
            columns = [c.name for c in Base.metadata.tables[table].columns]
            selected = ", ".join(f'"{c}"' for c in columns)
            rows = await conn.execute(
                text(f'SELECT {selected} FROM "{table}" ORDER BY id')
            )
            result[table] = [
                {column: canonical(value) for column, value in zip(columns, row)}
                for row in rows
            ]
    return result


async def validate_constraints() -> dict[str, Any]:
    async with engine.connect() as conn:
        fk_orphans = {}
        for child, child_col, parent, parent_col in FOREIGN_KEYS:
            value = await conn.scalar(
                text(
                    f'SELECT COUNT(*) FROM "{child}" c '
                    f'LEFT JOIN "{parent}" p ON c."{child_col}" = p."{parent_col}" '
                    f'WHERE c."{child_col}" IS NOT NULL '
                    f'AND p."{parent_col}" IS NULL'
                )
            )
            fk_orphans[f"{child}.{child_col}"] = int(value or 0)

        duplicates = {}
        for table, columns in UNIQUE_KEYS:
            cols = ", ".join(f'"{c}"' for c in columns)
            value = await conn.scalar(
                text(
                    f'SELECT COUNT(*) FROM ('
                    f'SELECT {cols}, COUNT(*) FROM "{table}" '
                    f'GROUP BY {cols} HAVING COUNT(*) > 1'
                    f') d'
                )
            )
            duplicates[f'{table}({", ".join(columns)})'] = int(value or 0)

        sequence_state = {}
        for table in TABLE_ORDER:
            seq = await conn.scalar(
                text(
                    f"SELECT pg_get_serial_sequence('public.{table}', 'id')"
                )
            )
            if seq is None:
                sequence_state[table] = {"sequence": None}
                continue
            sequence_name = str(seq).split(".", 1)[-1]
            # PostgreSQL 18's pg_catalog.pg_sequences view does not expose
            # the sequence's is_called flag. Read it from the sequence object
            # itself. The sequence name comes from pg_get_serial_sequence()
            # and is quoted as an identifier by SQLAlchemy's PostgreSQL dialect.
            sequence_identifier = sequence_name.split(".")[-1].strip('"')
            row = (
                await conn.execute(
                    text(
                        f'SELECT last_value, is_called '
                        f'FROM public."{sequence_identifier}"'
                    )
                )
            ).one()
            sequence_state[table] = {
                "sequence": str(seq),
                "last_value": int(row[0]),
                "is_called": bool(row[1]),
            }

        return {
            "foreign_key_orphans": fk_orphans,
            "unique_duplicate_groups": duplicates,
            "sequence_state": sequence_state,
        }


async def main() -> None:
    source = read_sqlite()
    target = await read_postgres()

    mismatches = []
    summary = {}

    for table in TABLE_ORDER:
        srows = source[table]
        trows = target[table]
        summary[table] = {
            "sqlite_count": len(srows),
            "postgres_count": len(trows),
            "count_match": len(srows) == len(trows),
        }

        if len(srows) != len(trows):
            mismatches.append(
                f"{table}: row count SQLite={len(srows)} PostgreSQL={len(trows)}"
            )
            continue

        for index, (srow, trow) in enumerate(zip(srows, trows)):
            if srow != trow:
                # Report only the first differing row and columns; do not dump
                # full potentially sensitive row contents.
                differing = [
                    column
                    for column in srow
                    if srow[column] != trow.get(column)
                ]
                mismatches.append(
                    f"{table}: row index {index}, id={srow.get('id')}, "
                    f"differing columns={differing}"
                )
                break

    constraints = await validate_constraints()
    bad_fk = {k: v for k, v in constraints["foreign_key_orphans"].items() if v}
    bad_unique = {
        k: v for k, v in constraints["unique_duplicate_groups"].items() if v
    }

    if bad_fk:
        mismatches.append(f"Foreign-key orphans: {bad_fk}")
    if bad_unique:
        mismatches.append(f"Unique-key duplicates: {bad_unique}")

    # Sequence validation: for non-empty integer-ID tables, the next generated
    # ID should be max(id)+1. We don't mutate sequences here.
    sequence_issues = {}
    for table in TABLE_ORDER:
        rows = target[table]
        state = constraints["sequence_state"][table]
        if not rows:
            continue
        expected_max = max(int(row["id"]) for row in rows)
        if state["last_value"] != expected_max or state["is_called"] is not True:
            sequence_issues[table] = {
                "expected_last_value": expected_max,
                "actual_last_value": state["last_value"],
                "is_called": state["is_called"],
            }
    if sequence_issues:
        mismatches.append(f"Sequence state mismatch: {sequence_issues}")

    report = {
        "source": str(SOURCE_PATH),
        "tables": summary,
        "constraints": constraints,
        "sequence_issues": sequence_issues,
        "mismatches": mismatches,
        "result": "PASS" if not mismatches else "FAIL",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(engine.dispose())
