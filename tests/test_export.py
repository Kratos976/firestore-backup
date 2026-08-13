# Copyright 2026 Federico Pannisco

from __future__ import annotations

import datetime as dt
import json

from firestore_backup.export import FirestoreExporter, records_to_tree, table_key_for
from firestore_backup.loader import iter_records
from firestore_backup.values import TYPE_KEY, decode, encode, encode_document
from tests.conftest import build_client


def test_recursive_export_and_pagination(fake_client):
    exporter = FirestoreExporter(fake_client, page_size=10)
    records = list(exporter.walk())

    assert len(records) == 32
    assert exporter.stats.collections == 5  # two distinct users/*/orders paths
    assert exporter.stats.errors == 0
    assert fake_client.root["products"].query_count >= 3

    paths = {r.path for r in records}
    assert "users/alice/orders/o1" in paths
    assert "users/bob/orders/o1" in paths
    assert "users/alice/orders/o1/lines/l1" in paths

    l1 = next(r for r in records if r.path.endswith("/lines/l1"))
    assert l1.table_key == "users__orders__lines"
    assert l1.depth == 2
    assert l1.parent_path == "users/alice/orders/o1"

    alice = next(r for r in records if r.path == "users/alice")
    assert alice.data["created"][TYPE_KEY] == "timestamp"
    assert alice.data["home"][TYPE_KEY] == "geopoint"
    assert alice.data["avatar"][TYPE_KEY] == "bytes"
    restored = decode(alice.data)
    assert restored["created"] == dt.datetime(2024, 1, 15, 10, 30, tzinfo=dt.timezone.utc)
    assert restored["avatar"] == b"\x00\x01\xff binary"

    # Entire record payload is plain JSON-safe data.
    json.dumps([r.to_json() for r in records], ensure_ascii=False)


def test_tree_round_trip_preserves_document_paths(records, tmp_path):
    path = tmp_path / "tree.json"
    tree = records_to_tree(iter(records))
    path.write_text(json.dumps({"collections": tree}), encoding="utf-8")
    loaded = list(iter_records(str(path)))
    assert sorted(r.path for r in loaded) == sorted(r.path for r in records)


def test_collection_group_recovers_known_orphan_subcollection():
    client = build_client()
    ghost = client.root["users"].orphan_parent("ghost")
    ghost.collection("orders").add("orphan-order", {"total": 99})

    normal = list(FirestoreExporter(client).walk())
    assert "users/ghost/orders/orphan-order" not in {r.path for r in normal}

    supplemented = list(
        FirestoreExporter(client, collection_group_names=["orders"]).walk()
    )
    paths = [r.path for r in supplemented]
    assert "users/ghost/orders/orphan-order" in paths
    # Existing orders are de-duplicated between recursive and group traversal.
    assert paths.count("users/alice/orders/o1") == 1
    assert paths.count("users/bob/orders/o1") == 1


def test_user_map_cannot_collide_with_typed_envelope():
    original = {
        "nested": {"__fs_type__": "timestamp", "value": "this is user data"},
        "another": {"__fs_type__": "anything", "x": 1},
    }
    encoded = encode_document(original)
    assert encoded["nested"][TYPE_KEY] == "map"
    assert decode(encoded) == original
    # Any user map carrying the marker key is escaped, including unknown values.
    assert decode(encode(original["another"])) == original["another"]


def test_export_options(fake_client):
    flat = list(FirestoreExporter(fake_client, include_subcollections=False).walk())
    assert len(flat) == 28

    limited = list(FirestoreExporter(fake_client, doc_limit=2, page_size=10).walk())
    assert sum(1 for r in limited if r.collection == "products") == 2

    filtered = list(FirestoreExporter(fake_client).walk(["products"]))
    assert {r.collection for r in filtered} == {"products"}

    named = list(FirestoreExporter(fake_client, subcollection_names=["orders"]).walk())
    assert not any(r.collection.endswith("/lines") for r in named)
    assert table_key_for("a/1/b/2/c") == "a__b__c"


def test_unknown_firestore_value_type_fails_loudly():
    class FutureFirestoreType:
        pass

    from firestore_backup.values import encode
    import pytest

    with pytest.raises(TypeError, match="unsupported Firestore value type"):
        encode(FutureFirestoreType())


def test_firestore_vector_keeps_type_marker():
    class Vector:
        def __init__(self, value):
            self.value = value

    vector = encode(Vector([1, 2.5, -3]))
    assert vector[TYPE_KEY] == "vector"
    assert vector["value"] == [1.0, 2.5, -3.0]


def test_default_depth_does_not_silently_stop_at_eight():
    client = build_client()
    doc = client.collection("deep").add("d0", {"level": 0})
    expected_last = "deep/d0"
    for level in range(1, 12):
        col = doc.collection(f"c{level}")
        doc = col.add(f"d{level}", {"level": level})
        expected_last = doc.path

    paths = {r.path for r in FirestoreExporter(client).walk(["deep"])}
    assert expected_last in paths


def test_depth_over_firestore_limit_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="between 0 and 100"):
        FirestoreExporter(build_client(), max_depth=101)
