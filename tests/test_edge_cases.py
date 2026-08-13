# Copyright 2026 Federico Pannisco

from __future__ import annotations

import json
import sqlite3

from firestore_backup.export import Record, records_to_tree
from firestore_backup.generate import generate_sql
from firestore_backup.values import TYPE_KEY, decode, encode_document


def _write_dump(tmp_path, record):
    path = tmp_path / "edge.json"
    path.write_text(json.dumps({"collections": records_to_tree(iter([record]))}), encoding="utf-8")
    return path


def test_envelope_like_map_survives_json_and_sql(tmp_path):
    original = {"__fs_type__": "timestamp", "value": "not-a-real-timestamp", "extra": 7}
    encoded = encode_document({"payload": original})
    assert encoded["payload"][TYPE_KEY] == "map"
    assert decode(encoded)["payload"] == original

    record = Record("events", "events", "one", "events/one", encoded)
    path = _write_dump(tmp_path, record)
    out = tmp_path / "edge.sql"
    with out.open("w", encoding="utf-8") as fh:
        generate_sql(str(path), fh, dialect="sqlite", map_mode="json")

    conn = sqlite3.connect(":memory:")
    conn.executescript(out.read_text(encoding="utf-8"))
    stored = conn.execute("SELECT payload FROM events").fetchone()[0]
    assert json.loads(stored) == original
    conn.close()


def test_unicode_and_reserved_sql_words(tmp_path):
    record = Record(
        "select",
        "select",
        "α",
        "select/α",
        {"from": "π", "sp ace": "✓", "123field": 123, "emoji😀": "ok"},
    )
    path = _write_dump(tmp_path, record)
    out = tmp_path / "unicode.sql"
    with out.open("w", encoding="utf-8") as fh:
        generate_sql(str(path), fh, dialect="sqlite")
    conn = sqlite3.connect(":memory:")
    conn.executescript(out.read_text(encoding="utf-8"))
    assert conn.execute('SELECT "from" FROM "select"').fetchone()[0] == "π"
    conn.close()
