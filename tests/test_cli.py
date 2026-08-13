# Copyright 2026 Federico Pannisco

from __future__ import annotations

import json
import sqlite3

import pytest

import firestore_backup.cli as cli
from firestore_backup.export import Record, Stats
from firestore_backup.loader import read_meta
from tests.conftest import build_client


def test_cli_export_json_ndjson_and_sql(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_client", lambda _creds: build_client())

    json_path = tmp_path / "dump.json"
    ndjson_path = tmp_path / "dump.ndjson"
    assert cli.main(["export", "--adc", "--project", "demo", "-o", str(json_path)]) == 0
    assert cli.main([
        "export", "--adc", "--project", "demo", "-o", str(ndjson_path), "--format", "ndjson"
    ]) == 0

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["__meta__"]["documents"] == 32
    assert payload["__meta__"]["errors"] == 0
    assert payload["__meta__"]["dump_schema"] == 1
    assert payload["__meta__"]["complete"] is True
    nd_meta = read_meta(str(ndjson_path))
    assert nd_meta["documents"] == 32
    assert nd_meta["complete"] is True

    sql_path = tmp_path / "schema.sql"
    assert cli.main(["sql", str(json_path), "-o", str(sql_path), "--dialect", "sqlite", "--drop"]) == 0
    conn = sqlite3.connect(":memory:")
    conn.executescript(sql_path.read_text(encoding="utf-8"))
    assert conn.execute("SELECT COUNT(*) FROM users__orders").fetchone()[0] == 3
    conn.close()


def test_export_failure_does_not_clobber_existing_backup(tmp_path, monkeypatch):
    target = tmp_path / "dump.json"
    target.write_text("known-good-backup", encoding="utf-8")

    class BrokenExporter:
        def __init__(self, *_args, **_kwargs):
            self.stats = Stats()

        def walk(self, _collections):
            yield Record("users", "users", "a", "users/a", {"x": 1})
            self.stats.documents = 1
            self.stats.collections = 1
            self.stats.errors = 1

    monkeypatch.setattr(cli, "build_client", lambda _creds: object())
    monkeypatch.setattr(cli, "FirestoreExporter", BrokenExporter)
    code = cli.main(["export", "--adc", "--project", "demo", "-o", str(target)])
    assert code == 1
    assert target.read_text(encoding="utf-8") == "known-good-backup"
    assert not list(tmp_path.glob(".*.tmp"))


def test_sql_failure_does_not_clobber_existing_output(tmp_path):
    dump = tmp_path / "broken.json"
    dump.write_text("{broken", encoding="utf-8")
    output = tmp_path / "schema.sql"
    output.write_text("known-good-sql", encoding="utf-8")
    assert cli.main(["sql", str(dump), "-o", str(output), "--dialect", "sqlite"]) == 1
    assert output.read_text(encoding="utf-8") == "known-good-sql"


def test_argument_validation():
    with pytest.raises(SystemExit) as exc:
        cli.main(["export", "--adc", "--limit", "0"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        cli.main(["sql", "dump.json", "--batch-size", "0"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        cli.main(["inspect", "dump.json", "--separator", ""])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        cli.main(["export", "--adc", "--max-depth", "101"])
    assert exc.value.code == 2


def test_output_paths_cannot_destroy_inputs_or_credentials(tmp_path, monkeypatch):
    key = tmp_path / "key.json"
    key.write_text('{"project_id":"demo"}', encoding="utf-8")
    monkeypatch.setattr(cli, "build_client", lambda _creds: build_client())
    assert cli.main(["export", "--credentials", str(key), "-o", str(key)]) == 2
    assert key.read_text(encoding="utf-8") == '{"project_id":"demo"}'

    dump = tmp_path / "dump.json"
    dump.write_text('{"collections":{}}', encoding="utf-8")
    assert cli.main(["sql", str(dump), "-o", str(dump)]) == 2
    assert dump.read_text(encoding="utf-8") == '{"collections":{}}'


def test_contradictory_subcollection_options_are_rejected(monkeypatch):
    monkeypatch.setattr(cli, "build_client", lambda _creds: build_client())
    assert cli.main([
        "export", "--adc", "--project", "demo", "--no-subcollections",
        "--subcollection-names", "orders"
    ]) == 2
