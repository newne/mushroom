#!/usr/bin/env python3
"""Deduplicate mushroom_batch_yield records by (room_id, in_date)."""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

src_dir = Path(__file__).resolve().parents[2]
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from global_const.global_const import ensure_src_path, pgsql_engine
from utils.loguru_setting import logger

ensure_src_path()


def _prepare_backup_table(backup_table: str) -> None:
    with pgsql_engine.connect() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {backup_table}
                (LIKE mushroom_batch_yield INCLUDING ALL)
                """
            )
        )
        conn.commit()


def _backup_duplicates(backup_table: str) -> int:
    with pgsql_engine.connect() as conn:
        result = conn.execute(
            text(
                f"""
                WITH duplicates AS (
                    SELECT room_id, in_date
                    FROM mushroom_batch_yield
                    GROUP BY room_id, in_date
                    HAVING COUNT(*) > 1
                )
                INSERT INTO {backup_table}
                SELECT b.*
                FROM mushroom_batch_yield b
                JOIN duplicates d
                    ON b.room_id = d.room_id
                    AND b.in_date = d.in_date
                WHERE NOT EXISTS (
                    SELECT 1 FROM {backup_table} bk WHERE bk.id = b.id
                )
                """
            )
        )
        conn.commit()
    return result.rowcount or 0


def _summarize_duplicates() -> list[tuple[str, str, int]]:
    with pgsql_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT room_id, in_date::text, COUNT(*) AS cnt
                FROM mushroom_batch_yield
                GROUP BY room_id, in_date
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC, room_id, in_date
                """
            )
        ).fetchall()
    return [(row[0], row[1], int(row[2])) for row in rows]


def dedupe_batch_yield(dry_run: bool = True) -> dict:
    report = {
        "dry_run": dry_run,
        "backup_table": None,
        "backup_rows": 0,
        "duplicate_groups": [],
        "deleted_rows": 0,
    }

    duplicates = _summarize_duplicates()
    report["duplicate_groups"] = duplicates
    if not duplicates:
        logger.info("[BATCH_YIELD_DEDUPE] No duplicates found")
        return report

    backup_table = f"mushroom_batch_yield_bak_{datetime.now().strftime('%Y%m%d')}"
    report["backup_table"] = backup_table

    if dry_run:
        logger.info(
            "[BATCH_YIELD_DEDUPE] Dry run: %s duplicate groups found",
            len(duplicates),
        )
        return report

    _prepare_backup_table(backup_table)
    backup_rows = _backup_duplicates(backup_table)
    report["backup_rows"] = backup_rows

    with pgsql_engine.connect() as conn:
        result = conn.execute(
            text(
                """
                WITH ranked AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY room_id, in_date
                            ORDER BY COALESCE(update_time, create_time) DESC NULLS LAST,
                                     id DESC
                        ) AS rn
                    FROM mushroom_batch_yield
                )
                DELETE FROM mushroom_batch_yield
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                """
            )
        )
        conn.commit()
    report["deleted_rows"] = result.rowcount or 0

    logger.info(
        "[BATCH_YIELD_DEDUPE] Backup rows: %s, deleted rows: %s",
        backup_rows,
        report["deleted_rows"],
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deduplicate mushroom_batch_yield by (room_id, in_date)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report duplicates; do not delete anything.",
    )
    args = parser.parse_args()

    report = dedupe_batch_yield(dry_run=args.dry_run)
    logger.info("[BATCH_YIELD_DEDUPE] Report: %s", report)


if __name__ == "__main__":
    main()
