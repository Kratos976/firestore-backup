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

"""Walk Firestore and emit JSON-safe document records.

Normal traversal starts at root collections and recursively follows each
existing document. Firestore can also retain subcollections under a missing
parent document; those cannot be discovered from a normal parent walk. Users
who know such collection IDs can add ``--collection-groups`` and the exporter
will query them directly, de-duplicating documents already reached normally.
"""

from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence

from .values import encode_document


def table_key_for(collection_path: str, separator: str = "__") -> str:
    """Human-readable collection pattern kept in the dump for compatibility.

    SQL inference no longer uses this string as identity; it derives a
    structured key from ``collection`` so names containing ``__`` cannot alias.
    """
    parts = [p for p in collection_path.split("/") if p]
    return separator.join(parts[::2])


@dataclass
class Record:
    collection: str
    table_key: str
    id: str
    path: str
    data: dict
    depth: int = 0
    parent_id: str | None = None
    parent_path: str | None = None

    def to_json(self) -> dict:
        out = {
            "collection": self.collection,
            "table_key": self.table_key,
            "id": self.id,
            "path": self.path,
            "depth": self.depth,
            "data": self.data,
        }
        if self.parent_id is not None:
            out["parent_id"] = self.parent_id
            out["parent_path"] = self.parent_path
        return out


@dataclass
class Stats:
    documents: int = 0
    collections: int = 0
    errors: int = 0
    started: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


class FirestoreExporter:
    """Recursively read Firestore collections and optional collection groups."""

    def __init__(
        self,
        client,
        *,
        include_subcollections: bool = True,
        subcollection_names: Sequence[str] | None = None,
        collection_group_names: Sequence[str] | None = None,
        read_time: _dt.datetime | None = None,
        max_depth: int = 100,
        doc_limit: int | None = None,
        page_size: int = 300,
        retries: int = 3,
        on_progress: Callable[[str, Stats], None] | None = None,
    ) -> None:
        if not 0 <= max_depth <= 100:
            raise ValueError("max_depth must be between 0 and 100")
        if doc_limit is not None and doc_limit <= 0:
            raise ValueError("doc_limit must be > 0")
        if page_size <= 0:
            raise ValueError("page_size must be > 0")
        if retries <= 0:
            raise ValueError("retries must be > 0")

        self.client = client
        self.include_subcollections = include_subcollections
        self.subcollection_names = list(subcollection_names) if subcollection_names else None
        self.collection_group_names = list(collection_group_names) if collection_group_names else []
        self.read_time = read_time
        self.max_depth = max_depth
        self.doc_limit = doc_limit
        self.page_size = page_size
        self.retries = retries
        self.on_progress = on_progress
        self.stats = Stats()
        self._seen_document_paths: set[str] = set()
        self._seen_collection_paths: set[str] = set()

    def root_collections(self) -> list:
        if self.read_time is not None:
            return list(self.client.collections(read_time=self.read_time))
        return list(self.client.collections())

    def _report(self, message: str) -> None:
        if self.on_progress:
            self.on_progress(message, self.stats)

    def _mark_collection(self, path: str) -> None:
        if path not in self._seen_collection_paths:
            self._seen_collection_paths.add(path)
            self.stats.collections += 1
            self._report(f"  -> {path}")

    def _paged_documents(self, query_ref, *, label: str) -> Iterator[Any]:
        """Keyset pagination over a collection/query with retry handling."""
        cursor = None
        fetched = 0

        while True:
            query = query_ref.order_by("__name__").limit(self.page_size)
            if cursor is not None:
                query = query.start_after(cursor)

            page: list = []
            for attempt in range(1, self.retries + 1):
                try:
                    page = list(
                        query.stream(read_time=self.read_time)
                        if self.read_time is not None
                        else query.stream()
                    )
                    break
                except Exception as exc:
                    if attempt == self.retries:
                        self.stats.errors += 1
                        self._report(f"  ! giving up on {label} after {attempt} attempts: {exc}")
                        return
                    time.sleep(min(2 ** attempt, 10))

            if not page:
                return

            for snapshot in page:
                yield snapshot
                fetched += 1
                if self.doc_limit is not None and fetched >= self.doc_limit:
                    return

            if len(page) < self.page_size:
                return
            cursor = page[-1]

    def _subcollections(self, doc_ref) -> Iterator[Any]:
        if self.subcollection_names is not None:
            for name in self.subcollection_names:
                yield doc_ref.collection(name)
            return
        try:
            if self.read_time is not None:
                yield from doc_ref.collections(read_time=self.read_time)
            else:
                yield from doc_ref.collections()
        except Exception as exc:
            self.stats.errors += 1
            self._report(f"  ! could not list subcollections of {doc_ref.path}: {exc}")

    @staticmethod
    def _depth_for_collection_path(path: str) -> int:
        parts = [p for p in path.split("/") if p]
        return max(0, (len(parts) - 1) // 2)

    @staticmethod
    def _collection_path(collection_ref) -> str:
        """Return the database-relative path of a Firestore collection."""
        parent_doc = getattr(collection_ref, "parent", None)

        if parent_doc is None:
            return collection_ref.id

        return f"{parent_doc.path}/{collection_ref.id}"

    def _record(self, snapshot, collection_ref, depth: int) -> Record:
        doc_ref = snapshot.reference
        raw = snapshot.to_dict()
        parent_doc = getattr(collection_ref, "parent", None)
        collection_path = self._collection_path(collection_ref)

        return Record(
            collection=collection_path,
            table_key=table_key_for(collection_path),
            id=snapshot.id,
            path=doc_ref.path,
            data=encode_document(raw),
            depth=depth,
            parent_id=getattr(parent_doc, "id", None),
            parent_path=getattr(parent_doc, "path", None),
        )

    def _emit_snapshot(self, snapshot, collection_ref, depth: int) -> Iterator[Record]:
        doc_ref = snapshot.reference
        if doc_ref.path in self._seen_document_paths:
            return
        self._seen_document_paths.add(doc_ref.path)

        record = self._record(snapshot, collection_ref, depth)
        self.stats.documents += 1
        if self.stats.documents % 500 == 0:
            self._report(f"     {self.stats.documents} documents ({self.stats.elapsed:.0f}s)")
        yield record

        if self.include_subcollections and depth < self.max_depth:
            for sub in self._subcollections(doc_ref):
                yield from self.walk_collection(sub, depth + 1)

    def walk_collection(self, collection_ref, depth: int = 0) -> Iterator[Record]:
        path = self._collection_path(collection_ref)
        self._mark_collection(path)
        for snapshot in self._paged_documents(collection_ref, label=path):
            yield from self._emit_snapshot(snapshot, collection_ref, depth)

    def walk_collection_group(self, name: str) -> Iterator[Record]:
        """Supplement normal traversal with one Firestore collection-group query."""
        if not hasattr(self.client, "collection_group"):
            self.stats.errors += 1
            self._report(f"  ! client does not support collection_group({name!r})")
            return

        self._report(f"  -> collection-group:{name}")
        query = self.client.collection_group(name)
        for snapshot in self._paged_documents(query, label=f"collection-group:{name}"):
            doc_ref = snapshot.reference
            collection_ref = getattr(doc_ref, "parent", None)
            if collection_ref is None:
                self.stats.errors += 1
                self._report(f"  ! could not determine collection for {doc_ref.path}")
                continue
            path = self._collection_path(collection_ref)
            self._mark_collection(path)
            depth = self._depth_for_collection_path(path)
            yield from self._emit_snapshot(snapshot, collection_ref, depth)

    def walk(self, collection_ids: Sequence[str] | None = None) -> Iterator[Record]:
        roots = self.root_collections()
        if collection_ids:
            wanted = set(collection_ids)
            roots = [c for c in roots if c.id in wanted]
            missing = wanted - {c.id for c in roots}
            if missing:
                self.stats.errors += len(missing)
                self._report(f"  ! no such root collection: {', '.join(sorted(missing))}")

        if not roots and not self.collection_group_names:
            self._report("  ! no collections found (empty database or wrong project/database)")

        for col in roots:
            yield from self.walk_collection(col, depth=0)

        for name in self.collection_group_names:
            yield from self.walk_collection_group(name)


# ----------------------------------------------------------------------
# Nested-tree assembly (for --format json)
# ----------------------------------------------------------------------
def records_to_tree(records: Iterator[Record]) -> dict:
    """Rebuild a nested tree from flat parent-first records.

    Records reached through ``--collection-groups`` may have a missing parent;
    those are preserved as top-level containers keyed by their full collection
    path rather than silently discarded.
    """
    tree: dict = {}
    index: dict[str, dict] = {}

    for rec in records:
        node = {"id": rec.id, "path": rec.path, "data": rec.data}

        if rec.depth == 0 or not rec.parent_path:
            container = tree.setdefault(rec.collection, {"path": rec.collection, "documents": []})
        else:
            parent = index.get(rec.parent_path)
            if parent is None:
                container = tree.setdefault(rec.collection, {"path": rec.collection, "documents": []})
            else:
                subs = parent.setdefault("subcollections", {})
                container = subs.setdefault(
                    rec.collection.rsplit("/", 1)[-1],
                    {"path": rec.collection, "documents": []},
                )

        container["documents"].append(node)
        index[rec.path] = node

    return tree
