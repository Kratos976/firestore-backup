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

"""Minimal in-memory Firestore surface used by the test suite."""

from __future__ import annotations


class GeoPoint:
    def __init__(self, latitude, longitude):
        self.latitude = latitude
        self.longitude = longitude


class DocumentReference:
    def __init__(self, doc, client):
        self._doc = doc
        self._client = client

    @property
    def id(self):
        return self._doc.id

    @property
    def path(self):
        return self._doc.path

    @property
    def parent(self):
        return CollectionReference(self._doc.parent, self._client)

    def collections(self, read_time=None):
        return [CollectionReference(col, self._client) for col in self._doc.subcollections.values()]

    def collection(self, name):
        return CollectionReference(self._doc.collection(name), self._client)


class DocumentSnapshot:
    def __init__(self, doc, client):
        self._doc = doc
        self._client = client

    @property
    def id(self):
        return self._doc.id

    @property
    def reference(self):
        return DocumentReference(self._doc, self._client)

    def to_dict(self):
        return self._doc.data


class Query:
    def __init__(self, collection, client, limit=None, after=None):
        self._collection = collection
        self._client = client
        self._limit = limit
        self._after = after

    def order_by(self, _field):
        return self

    def limit(self, count):
        return Query(self._collection, self._client, count, self._after)

    def start_after(self, snapshot):
        return Query(self._collection, self._client, self._limit, snapshot.id)

    def stream(self, read_time=None):
        docs = sorted(self._collection.documents.values(), key=lambda d: d.id)
        if self._after is not None:
            docs = [d for d in docs if d.id > self._after]
        if self._limit is not None:
            docs = docs[: self._limit]
        self._collection.query_count += 1
        return iter(DocumentSnapshot(d, self._client) for d in docs)


class CollectionGroupQuery:
    def __init__(self, client, name, limit=None, after=None):
        self._client = client
        self._name = name
        self._limit = limit
        self._after = after

    def order_by(self, _field):
        return self

    def limit(self, count):
        return CollectionGroupQuery(self._client, self._name, count, self._after)

    def start_after(self, snapshot):
        return CollectionGroupQuery(self._client, self._name, self._limit, snapshot.reference.path)

    def stream(self, read_time=None):
        docs = []
        for col in self._client._collections:
            if col.id == self._name:
                docs.extend(col.documents.values())
        docs.sort(key=lambda d: d.path)
        if self._after is not None:
            docs = [d for d in docs if d.path > self._after]
        if self._limit is not None:
            docs = docs[: self._limit]
        return iter(DocumentSnapshot(d, self._client) for d in docs)


class CollectionReference(Query):
    def __init__(self, collection, client):
        super().__init__(collection, client)

    @property
    def id(self):
        return self._collection.id

    @property
    def parent(self):
        parent_doc = self._collection.parent
        if parent_doc is None:
            return None
        return DocumentReference(parent_doc, self._client)


class FakeDocument:
    def __init__(self, doc_id, data, parent_collection):
        self.id = doc_id
        self.data = data
        self.parent = parent_collection
        self.subcollections: dict = {}

    @property
    def path(self):
        return f"{self.parent.path}/{self.id}"

    def collection(self, name):
        col = self.subcollections.get(name)
        if col is None:
            col = FakeCollection(self.parent.client, name, parent=self)
            self.subcollections[name] = col
        return col


class FakeCollection:
    def __init__(self, client, col_id, parent=None):
        self.client = client
        self.id = col_id
        self.parent = parent
        self.documents: dict = {}
        self.query_count = 0
        client._collections.append(self)

    @property
    def path(self):
        return f"{self.parent.path}/{self.id}" if self.parent else self.id

    def add(self, doc_id, data):
        doc = FakeDocument(doc_id, data, self)
        self.documents[doc_id] = doc
        return doc

    def orphan_parent(self, doc_id):
        """Create a parent document object without making it query-visible."""
        return FakeDocument(doc_id, {}, self)


class FakeClient:
    def __init__(self):
        self.root: dict = {}
        self._collections: list[FakeCollection] = []

    def collection(self, name):
        col = self.root.get(name)
        if col is None:
            col = FakeCollection(self, name)
            self.root[name] = col
        return col

    def collections(self, read_time=None):
        return [CollectionReference(c, self) for c in self.root.values()]

    def collection_group(self, name):
        return CollectionGroupQuery(self, name)
