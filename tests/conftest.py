# Copyright 2026 Federico Pannisco

from __future__ import annotations

import datetime as dt
import json

import pytest

from firestore_backup.export import FirestoreExporter, records_to_tree
from tests.mock_firestore import FakeClient, GeoPoint


def build_client():
    client = FakeClient()

    users = client.collection("users")
    alice = users.add("alice", {
        "name": "Alice",
        "age": 30,
        "score": 9.5,
        "active": True,
        "deleted": None,
        "created": dt.datetime(2024, 1, 15, 10, 30, tzinfo=dt.timezone.utc),
        "home": GeoPoint(41.9028, 12.4964),
        "avatar": b"\x00\x01\xff binary",
        "tags": ["admin", "beta"],
        "address": {"city": "Roma", "zip": "00100", "geo": {"floor": 3}},
        "weird key!": "sanitize me",
    })
    bob = users.add("bob", {
        "name": "Bob",
        "age": 41,
        "nickname": "bobby",
        "score": 7,
        "tags": [],
        "address": {"city": "Milano"},
    })
    users.add("carol", {})

    alice_orders = alice.collection("orders")
    alice_o1 = alice_orders.add("o1", {"total": 12.5, "items": ["x", "y"]})
    alice_orders.add("o2", {"total": 3.0, "items": []})
    # Same document ID under a different parent: valid Firestore and a critical
    # regression case for SQL identity.
    bob.collection("orders").add("o1", {"total": 1.0, "items": ["z"]})

    alice_o1.collection("lines").add("l1", {"sku": "ABC", "qty": 2})

    products = client.collection("products")
    for i in range(25):
        products.add(f"p{i:03d}", {"sku": f"SKU-{i}", "price": i * 1.5})

    return client


@pytest.fixture
def fake_client():
    return build_client()


@pytest.fixture
def records(fake_client):
    return list(FirestoreExporter(fake_client, page_size=10).walk())


@pytest.fixture
def dumps(tmp_path, records):
    json_path = tmp_path / "dump.json"
    ndjson_path = tmp_path / "dump.ndjson"

    json_path.write_text(
        json.dumps(
            {"__meta__": {"generator": "test"}, "collections": records_to_tree(iter(records))},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    lines = [json.dumps({"type": "meta", "generator": "test"})]
    lines.extend(json.dumps(record.to_json(), ensure_ascii=False) for record in records)
    lines.append(json.dumps({"type": "meta", "generator": "test", "final": True, "documents": len(records)}))
    ndjson_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, ndjson_path
