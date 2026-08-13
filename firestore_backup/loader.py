# Copyright 2026 Federico Pannisco
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Read nested JSON or streaming NDJSON dumps into a flat Record sequence."""

from __future__ import annotations

import json
from typing import Iterator

from .export import Record, table_key_for

META_KEY = "__meta__"


def detect_format(path: str) -> str:
    if path.endswith((".ndjson", ".jsonl")):
        return "ndjson"

    with open(path, "r", encoding="utf-8") as fh:
        first = fh.readline().strip()

    if not first or first in ("{", "["):
        return "json"
    try:
        obj = json.loads(first)
    except json.JSONDecodeError:
        return "json"
    if isinstance(obj, dict) and ("collections" in obj or META_KEY in obj):
        return "json"
    return "ndjson"


def _validate_record(
    *,
    source: str,
    collection: object,
    doc_id: object,
    path: object,
    data: object,
    depth: object,
    parent_id: object = None,
    parent_path: object = None,
) -> Record:
    if not isinstance(collection, str) or not collection:
        raise ValueError(f"{source}: collection must be a non-empty string")
    if not isinstance(doc_id, str) or not doc_id:
        raise ValueError(f"{source}: id must be a non-empty string")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{source}: path must be a non-empty string")
    if not isinstance(data, dict):
        raise ValueError(f"{source}: data must be a JSON object")

    col_parts = collection.split("/")
    doc_parts = path.split("/")
    if any(part == "" for part in col_parts) or len(col_parts) % 2 != 1:
        raise ValueError(f"{source}: invalid Firestore collection path {collection!r}")
    if any(part == "" for part in doc_parts) or len(doc_parts) % 2 != 0:
        raise ValueError(f"{source}: invalid Firestore document path {path!r}")
    expected_path = f"{collection}/{doc_id}"
    if path != expected_path:
        raise ValueError(
            f"{source}: path/id mismatch; expected {expected_path!r}, got {path!r}"
        )

    expected_depth, expected_parent_id, expected_parent_path = _collection_context(collection)
    try:
        depth_value = int(depth)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source}: depth must be an integer") from exc
    if depth_value != expected_depth:
        raise ValueError(
            f"{source}: depth mismatch; expected {expected_depth}, got {depth_value}"
        )

    if expected_depth > 0:
        if parent_id is None:
            parent_id = expected_parent_id
        if parent_path is None:
            parent_path = expected_parent_path
        if parent_id != expected_parent_id or parent_path != expected_parent_path:
            raise ValueError(f"{source}: parent metadata does not match collection path")
    else:
        parent_id = None
        parent_path = None

    return Record(
        collection=collection,
        table_key=table_key_for(collection),
        id=doc_id,
        path=path,
        data=data,
        depth=depth_value,
        parent_id=parent_id,
        parent_path=parent_path,
    )


def _iter_ndjson(path: str) -> Iterator[Record]:
    versioned = False
    complete = False
    final_errors = 0

    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON ({exc})") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{line_no}: expected a JSON object")

            if obj.get("type") == "meta" or META_KEY in obj:
                if obj.get("dump_schema") == 1:
                    versioned = True
                    if obj.get("final") is True and obj.get("complete") is True:
                        complete = True
                        final_errors = int(obj.get("errors", 0) or 0)
                continue

            try:
                collection = obj["collection"]
                doc_id = obj["id"]
            except KeyError as exc:
                raise ValueError(f"{path}:{line_no}: missing required field {exc.args[0]!r}") from exc
            record_path = obj.get("path", f"{collection}/{doc_id}")
            yield _validate_record(
                source=f"{path}:{line_no}",
                collection=collection,
                doc_id=doc_id,
                path=record_path,
                data=obj.get("data", {}),
                depth=obj.get("depth", _collection_context(collection)[0] if isinstance(collection, str) else 0),
                parent_id=obj.get("parent_id"),
                parent_path=obj.get("parent_path"),
            )

    if versioned and not complete:
        raise ValueError(
            f"{path}: incomplete or truncated firestore-backup NDJSON dump "
            "(final completion marker missing)"
        )
    if final_errors:
        raise ValueError(f"{path}: dump reports {final_errors} export error(s)")


def _collection_context(col_path: str) -> tuple[int, str | None, str | None]:
    parts = [p for p in col_path.split("/") if p]
    depth = max(0, (len(parts) - 1) // 2)
    if depth == 0:
        return 0, None, None
    return depth, parts[-2], "/".join(parts[:-1])


def _walk_tree(collections: dict, *, source: str = "JSON dump") -> Iterator[Record]:
    for name, container in collections.items():
        if not isinstance(container, dict):
            raise ValueError(f"{source}: collection container {name!r} must be an object")
        col_path = container.get("path", name)
        if not isinstance(col_path, str):
            raise ValueError(f"{source}: collection path must be a string")
        depth, fallback_parent_id, fallback_parent_path = _collection_context(col_path)
        documents = container.get("documents", [])
        if not isinstance(documents, list):
            raise ValueError(f"{source}: documents for {col_path!r} must be an array")
        for index, doc in enumerate(documents):
            if not isinstance(doc, dict):
                raise ValueError(f"{source}: document #{index} in {col_path!r} must be an object")
            if "id" not in doc:
                raise ValueError(f"{source}: document #{index} in {col_path!r} has no id")
            doc_id = doc["id"]
            doc_path = doc.get("path", f"{col_path}/{doc_id}")
            record = _validate_record(
                source=f"{source}:{doc_path}",
                collection=col_path,
                doc_id=doc_id,
                path=doc_path,
                data=doc.get("data", {}),
                depth=depth,
                parent_id=fallback_parent_id,
                parent_path=fallback_parent_path,
            )
            yield record
            subs = doc.get("subcollections")
            if subs is not None:
                if not isinstance(subs, dict):
                    raise ValueError(f"{source}:{doc_path}: subcollections must be an object")
                yield from _walk_tree(subs, source=source)


def iter_records(path: str, fmt: str | None = None) -> Iterator[Record]:
    fmt = fmt or detect_format(path)
    if fmt not in ("json", "ndjson"):
        raise ValueError("format must be 'json' or 'ndjson'")

    if fmt == "ndjson":
        yield from _iter_ndjson(path)
        return

    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: unexpected structure - expected a JSON object")

    meta = payload.get(META_KEY, {})
    if isinstance(meta, dict) and meta.get("dump_schema") == 1:
        if meta.get("complete") is not True:
            raise ValueError(f"{path}: incomplete firestore-backup JSON dump")
        errors = int(meta.get("errors", 0) or 0)
        if errors:
            raise ValueError(f"{path}: dump reports {errors} export error(s)")

    collections = payload.get("collections", payload)
    if not isinstance(collections, dict):
        raise ValueError(f"{path}: unexpected structure - expected a 'collections' object")
    yield from _walk_tree(collections, source=path)


def read_meta(path: str) -> dict:
    """Read dump metadata; for NDJSON prefer the final summary meta record."""
    try:
        fmt = detect_format(path)
        if fmt == "ndjson":
            last_meta: dict = {}
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("type") == "meta":
                        last_meta = obj
            return last_meta

        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload.get(META_KEY, {}) if isinstance(payload, dict) else {}
    except Exception:
        return {}
