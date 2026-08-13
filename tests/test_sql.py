# Copyright 2026 Federico Pannisco

from __future__ import annotations

import io
import json
import sqlite3

from firestore_backup.export import Record, records_to_tree
from firestore_backup.generate import generate_sql
from firestore_backup.infer import SchemaBuilder, collection_key, document_pk
from firestore_backup.loader import detect_format, iter_records, read_meta
from firestore_backup.sql import get_dialect


def render(path, **kwargs):
    buf = io.StringIO()
    generate_sql(str(path), buf, None, on_progress=lambda _m: None, **kwargs)
    return buf.getvalue()


def table_by_sql_name(path, **kwargs):
    builder = SchemaBuilder(**kwargs)
    for record in iter_records(str(path)):
        builder.observe(record)
    return {t.sql_name: t for t in builder.finalize()}


def test_loader_formats_and_metadata(dumps):
    json_path, ndjson_path = dumps
    assert detect_format(str(json_path)) == "json"
    assert detect_format(str(ndjson_path)) == "ndjson"
    assert len(list(iter_records(str(json_path)))) == 32
    assert len(list(iter_records(str(ndjson_path)))) == 32
    assert read_meta(str(ndjson_path))["documents"] == 32


def test_sqlite_round_trip_uses_full_path_identity(dumps):
    json_path, _ = dumps
    sql_text = render(json_path, dialect="sqlite", drop=True)
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql_text)

    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM users__orders").fetchone()[0] == 3

    duplicate_ids = conn.execute(
        "SELECT _id, _path, _pk FROM users__orders WHERE _id='o1' ORDER BY _path"
    ).fetchall()
    assert [r[1] for r in duplicate_ids] == [
        "users/alice/orders/o1",
        "users/bob/orders/o1",
    ]
    assert duplicate_ids[0][2] != duplicate_ids[1][2]
    assert duplicate_ids[0][2] == document_pk("users/alice/orders/o1")

    # Parent joins use the full-path hash, not the ambiguous document ID.
    joined = conn.execute(
        "SELECT u._id, o._path FROM users u JOIN users__orders o "
        "ON o._parent_pk = u._pk WHERE o._id='o1' ORDER BY u._id"
    ).fetchall()
    assert joined == [
        ("alice", "users/alice/orders/o1"),
        ("bob", "users/bob/orders/o1"),
    ]

    row = conn.execute(
        "SELECT name, age, score, active, home_lat, address_city, tags, deleted, avatar "
        "FROM users WHERE _id='alice'"
    ).fetchone()
    assert row[:8] == ("Alice", 30, 9.5, 1, 41.9028, "Roma", '["admin", "beta"]', None)
    assert row[8] == b"\x00\x01\xff binary"
    conn.close()


def test_reserved_user_fields_and_flatten_collisions_are_preserved(tmp_path):
    record = Record(
        collection="tricky",
        table_key="tricky",
        id="doc1",
        path="tricky/doc1",
        data={
            "_pk": "user-pk",
            "_id": "user-id",
            "_path": "user-path",
            "a": {"b": 1},
            "a_b": 2,
        },
    )
    path = tmp_path / "tricky.json"
    path.write_text(json.dumps({"collections": records_to_tree(iter([record]))}), encoding="utf-8")

    sql_text = render(path, dialect="sqlite")
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql_text)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(tricky)")]

    assert "_pk" in cols and "_pk_2" in cols
    assert "_id" in cols and "_id_2" in cols
    assert "_path" in cols and "_path_2" in cols
    assert "a_b" in cols and "a_b_2" in cols

    row = conn.execute(
        "SELECT _id, _id_2, _path_2, _pk_2, a_b, a_b_2 FROM tricky"
    ).fetchone()
    assert row[0] == "doc1"
    assert row[1:4] == ("user-id", "user-path", "user-pk")
    assert set(row[4:]) == {1, 2}
    conn.close()


def test_collection_table_name_collision_is_disambiguated(tmp_path):
    records = [
        Record("a__b", "a__b", "root", "a__b/root", {"kind": "root"}),
        Record("a/x/b", "a__b", "child", "a/x/b/child", {"kind": "child"},
               depth=1, parent_id="x", parent_path="a/x"),
    ]
    path = tmp_path / "table-collision.json"
    path.write_text(json.dumps({"collections": records_to_tree(iter(records))}), encoding="utf-8")

    tables = table_by_sql_name(path)
    assert set(tables) == {"a__b", "a__b_2"}
    assert len({t.key for t in tables.values()}) == 2

    conn = sqlite3.connect(":memory:")
    conn.executescript(render(path, dialect="sqlite", add_foreign_keys=False))
    counts = sorted(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in tables)
    assert counts == [1, 1]
    conn.close()


def test_dialect_specific_ddl(dumps):
    json_path, _ = dumps
    pg = render(json_path, dialect="postgres")
    my = render(json_path, dialect="mysql")
    lite = render(json_path, dialect="sqlite")
    generic = render(json_path, dialect="generic")

    assert '"_pk" CHAR(64) PRIMARY KEY' in pg
    assert 'FOREIGN KEY ("_parent_pk")' in pg
    assert "JSONB" in pg and "TIMESTAMPTZ" in pg and "decode(" in pg

    assert "`_pk` CHAR(64) PRIMARY KEY" in my
    assert "CREATE INDEX IF NOT EXISTS" not in my
    assert "CREATE INDEX `idx_users__orders_parent`" in my
    assert "INSERT IGNORE" not in render(json_path, dialect="mysql", on_conflict=True)
    assert "ON DUPLICATE KEY UPDATE" in render(json_path, dialect="mysql", on_conflict=True)
    assert "LONGTEXT" in my
    assert "DATETIME(6)" in my and "FROM_BASE64(" in my

    assert "ADD CONSTRAINT" not in lite
    assert "X'" in lite
    assert "JSONB" not in generic and "FROM_BASE64" not in generic


def test_arrays_table_uses_parent_pk(dumps):
    json_path, _ = dumps
    conn = sqlite3.connect(":memory:")
    conn.executescript(render(json_path, dialect="sqlite", array_mode="table"))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users__tags)")]
    assert cols == ["_parent_pk", "_parent_id", "_parent_path", "_idx", "_value"]
    rows = conn.execute(
        "SELECT _value FROM users__tags WHERE _parent_pk=? ORDER BY _idx",
        (document_pk("users/alice"),),
    ).fetchall()
    assert [r[0] for r in rows] == ["admin", "beta"]
    conn.close()


def test_skip_existing_is_re_runnable(dumps):
    json_path, _ = dumps
    conn = sqlite3.connect(":memory:")
    conn.executescript(render(json_path, dialect="sqlite"))
    conn.executescript(render(json_path, dialect="sqlite", on_conflict=True))
    assert conn.execute("SELECT COUNT(*) FROM users__orders").fetchone()[0] == 3
    conn.close()


def test_literal_escaping():
    pg = get_dialect("postgres")
    my = get_dialect("mysql")
    assert pg.string("O'Brien") == "'O''Brien'"
    import pytest

    with pytest.raises(ValueError, match="NUL byte"):
        pg.string("a\x00b")
    assert my.string("a\\b") == "'a\\\\b'"


def test_special_firestore_doubles_are_not_silently_null(tmp_path):
    record = Record(
        collection="metrics", table_key="metrics", id="m1", path="metrics/m1",
        data={"value": {"__fs_type__": "double", "value": "NaN"}},
    )
    path = tmp_path / "special.json"
    path.write_text(json.dumps({"collections": records_to_tree(iter([record]))}), encoding="utf-8")
    sql_text = render(path, dialect="sqlite")
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql_text)
    assert conn.execute("SELECT value FROM metrics").fetchone()[0] == "NaN"
    conn.close()


def test_sql_column_names_are_deterministic_across_field_order(tmp_path):
    def schema_for(data, name):
        record = Record("docs", "docs", "d1", "docs/d1", data)
        path = tmp_path / name
        path.write_text(json.dumps({"collections": records_to_tree(iter([record]))}), encoding="utf-8")
        return [c.sql_name for c in table_by_sql_name(path)["docs"].columns.values()]

    first = schema_for({"a": {"b": 1}, "a_b": 2, "z": 3}, "first.json")
    second = schema_for({"z": 3, "a_b": 2, "a": {"b": 1}}, "second.json")
    assert first == second


def test_long_foreign_key_names_do_not_collapse(tmp_path):
    import re

    root = "root_" + "x" * 55
    child_a = "child_" + "a" * 55
    child_b = "child_" + "b" * 55
    records = [
        Record(root, root, "p", f"{root}/p", {"v": 1}),
        Record(f"{root}/p/{child_a}", "x", "a", f"{root}/p/{child_a}/a", {"v": 1},
               depth=1, parent_id="p", parent_path=f"{root}/p"),
        Record(f"{root}/p/{child_b}", "y", "b", f"{root}/p/{child_b}/b", {"v": 1},
               depth=1, parent_id="p", parent_path=f"{root}/p"),
    ]
    path = tmp_path / "long-names.json"
    path.write_text(json.dumps({"collections": records_to_tree(iter(records))}), encoding="utf-8")
    sql_text = render(path, dialect="mysql")
    names = re.findall(r"ADD CONSTRAINT `([^`]+)`", sql_text)
    assert len(names) == 2
    assert len(set(names)) == 2
    assert all(len(name) <= 64 for name in names)


def test_versioned_ndjson_requires_final_completion_marker(tmp_path):
    import pytest

    path = tmp_path / "truncated.ndjson"
    path.write_text(
        '\n'.join([
            json.dumps({"type":"meta", "dump_schema":1, "complete":False}),
            json.dumps({"collection":"users", "id":"a", "path":"users/a", "depth":0, "data":{"x":1}}),
        ]) + '\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="incomplete or truncated"):
        list(iter_records(str(path)))


def test_versioned_json_rejects_incomplete_dump(tmp_path):
    import pytest

    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps({
        "__meta__": {"dump_schema": 1, "complete": False, "errors": 1},
        "collections": {},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        list(iter_records(str(path)))


def test_loader_rejects_path_identity_mismatch(tmp_path):
    import pytest

    path = tmp_path / "bad-path.ndjson"
    path.write_text(json.dumps({
        "collection":"users", "id":"alice", "path":"users/bob", "depth":0, "data":{}
    }) + '\n', encoding="utf-8")
    with pytest.raises(ValueError, match="path/id mismatch"):
        list(iter_records(str(path), "ndjson"))


def test_array_table_preserves_empty_vs_absent_with_count(tmp_path):
    records = [
        Record("docs", "docs", "empty", "docs/empty", {"tags": []}),
        Record("docs", "docs", "missing", "docs/missing", {}),
        Record("docs", "docs", "filled", "docs/filled", {"tags": ["a", "b"]}),
    ]
    path = tmp_path / "arrays.json"
    path.write_text(json.dumps({"collections": records_to_tree(iter(records))}), encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.executescript(render(path, dialect="sqlite", array_mode="table"))
    counts = dict(conn.execute('SELECT _id, tags_count FROM docs'))
    assert counts == {"empty": 0, "missing": None, "filled": 2}
    assert conn.execute('SELECT COUNT(*) FROM docs__tags').fetchone()[0] == 2
    conn.close()


def test_actual_collection_keeps_base_name_when_array_table_collides(tmp_path):
    records = [
        Record("users", "users", "u1", "users/u1", {"orders": [1, 2]}),
        Record("users/u1/orders", "users__orders", "o1", "users/u1/orders/o1", {"x": 1},
               depth=1, parent_id="u1", parent_path="users/u1"),
    ]
    path = tmp_path / "array-table-collision.json"
    path.write_text(json.dumps({"collections": records_to_tree(iter(records))}), encoding="utf-8")
    builder = SchemaBuilder(array_mode="table")
    for record in iter_records(str(path)):
        builder.observe(record)
    tables = builder.finalize()
    collection_table = next(t for t in tables if not t.is_array_table and t.depth == 1)
    array_table = next(t for t in tables if t.is_array_table)
    assert collection_table.sql_name == "users__orders"
    assert array_table.sql_name == "users__orders_2"
