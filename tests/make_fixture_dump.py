# Copyright 2026 Federico Pannisco

"""Create a deterministic synthetic dump for real-engine CI smoke tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow direct execution as ``python tests/make_fixture_dump.py`` from a source
# checkout without requiring the package to have been installed first.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firestore_backup.export import FirestoreExporter, records_to_tree
from tests.conftest import build_client


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    args = parser.parse_args()

    client = build_client()
    client.collection("limits").add("long-text", {"text": "x" * 70000})
    records = list(FirestoreExporter(client, page_size=10).walk())
    payload = {
        "__meta__": {"generator": "firestore-backup CI fixture", "documents": len(records)},
        "collections": records_to_tree(iter(records)),
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
