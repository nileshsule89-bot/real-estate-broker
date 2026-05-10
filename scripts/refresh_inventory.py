#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, ensure_database_schema
from app.services.inventory_refresh_service import refresh_location_inventory


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh property inventory for a location.")
    parser.add_argument("--location", required=True, help="Location to refresh, for example Airoli")
    parser.add_argument(
        "--no-mark-missing",
        action="store_true",
        help="Do not mark existing listings inactive when they are absent from the latest refresh batch.",
    )
    args = parser.parse_args()

    ensure_database_schema()
    db = SessionLocal()
    try:
        rows = refresh_location_inventory(db, args.location, mark_missing=not args.no_mark_missing)
        summary = {
            "location": args.location,
            "loaded": len(rows),
            "sources": sorted({row.source for row in rows}),
            "rent": sum(1 for row in rows if row.type == "rent"),
            "buy": sum(1 for row in rows if row.type == "buy"),
        }
        print(json.dumps(summary, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
