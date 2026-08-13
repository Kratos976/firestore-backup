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

"""Infer a relational schema from Firestore records without losing identity.

The important design rule is that *internal identity is structured*. Human SQL
names are only assigned at the very end.  This prevents three classes of data
loss that string-concatenation based flatteners commonly suffer from:

* ``{"a": {"b": 1}, "a_b": 2}`` keeps both values;
* a user field named ``_id`` / ``_path`` never overwrites generated metadata;
* a root collection named ``a__b`` never aliases subcollection ``a/*/b``.

Every document row has a deterministic ``_pk`` (SHA-256 of its full Firestore
path). ``_id`` remains the original Firestore document ID for readability.
Child tables link with ``_parent_pk`` so identical document IDs under different
parents remain unambiguous.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Hashable, Iterable

from .values import (
    T_BYTES,
    T_DOUBLE,
    T_GEOPOINT,
    T_MAP,
    T_REFERENCE,
    T_TIMESTAMP,
    T_VECTOR,
    envelope_type,
    is_envelope,
)

# Logical column kinds, independent of SQL dialect.
NULL, BOOL, INT, FLOAT, STRING = "null", "bool", "int", "float", "string"
TIMESTAMP, BYTES, REFERENCE, JSON = "timestamp", "bytes", "reference", "json"

MISSING = object()

META_NAMES = (
    "_pk",
    "_id",
    "_path",
    "_parent_pk",
    "_parent_id",
    "_parent_path",
    "_idx",
    "_value",
)

# Internal keys are deliberately different Python shapes from field keys, so a
# Firestore field can never collide with generated metadata even if the text is
# identical.
def meta_key(name: str) -> tuple[str, str]:
    return ("meta", name)


def document_pk(path: str) -> str:
    """Stable SQL key derived from the complete Firestore document path."""
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def field_key(path: tuple[str, ...], component: str | None = None) -> tuple[tuple[str, ...], str | None]:
    return (path, component)


def collection_key(collection_path: str) -> tuple[str, tuple[str, ...]]:
    parts = tuple(p for p in collection_path.split("/") if p)
    return ("collection", parts[::2])


def collection_label(collection_path: str, separator: str = "__") -> str:
    return separator.join(collection_key(collection_path)[1])


def parent_collection_key(collection_path: str):
    key = collection_key(collection_path)
    names = key[1]
    if len(names) <= 1:
        return None
    return ("collection", names[:-1])


def array_key(parent_key: Hashable, field_storage_key: Hashable) -> tuple[str, Hashable, Hashable]:
    return ("array", parent_key, field_storage_key)


@dataclass(frozen=True)
class Cell:
    key: Hashable
    name: str
    kind: str
    value: Any


@dataclass(frozen=True)
class ArrayField:
    key: Hashable
    name: str
    values: list


# ----------------------------------------------------------------------
# flattening
# ----------------------------------------------------------------------
def _is_scalar(value: Any) -> bool:
    if is_envelope(value):
        return envelope_type(value) in (T_TIMESTAMP, T_BYTES, T_REFERENCE, T_DOUBLE)
    return value is None or isinstance(value, (str, bool, int, float))


def _join(path: tuple[str, ...], separator: str) -> str:
    return separator.join(path)


def _cell(
    path: tuple[str, ...],
    value: Any,
    cells: list[Cell],
    arrays: list[ArrayField],
    *,
    separator: str,
    map_mode: str,
    array_mode: str,
) -> None:
    name = _join(path, separator)

    if is_envelope(value):
        kind = envelope_type(value)
        if kind == T_TIMESTAMP:
            cells.append(Cell(field_key(path), name, TIMESTAMP, value.get("value")))
        elif kind == T_BYTES:
            cells.append(Cell(field_key(path), name, BYTES, value.get("value")))
        elif kind == T_REFERENCE:
            cells.append(Cell(field_key(path), name, REFERENCE, value.get("path")))
        elif kind == T_DOUBLE:
            # Preserve Firestore NaN/Infinity instead of silently converting
            # them to NULL. Treating the column as text is portable; if finite
            # doubles are also observed, type reconciliation keeps all values.
            cells.append(Cell(field_key(path), name, STRING, value.get("value")))
        elif kind == T_GEOPOINT:
            cells.append(
                Cell(field_key(path, "lat"), f"{name}{separator}lat", FLOAT, value.get("latitude"))
            )
            cells.append(
                Cell(field_key(path, "lng"), f"{name}{separator}lng", FLOAT, value.get("longitude"))
            )
        elif kind == T_MAP:
            mapped = value.get("value") or {}
            if map_mode == "flatten" and mapped:
                for key, sub in mapped.items():
                    _cell(path + (str(key),), sub, cells, arrays,
                          separator=separator, map_mode=map_mode, array_mode=array_mode)
            else:
                cells.append(Cell(field_key(path), name, JSON, mapped))
        elif kind == T_VECTOR:
            # SQL dialects do not share a native vector type. Keep the marker
            # and values in JSON so vector semantics are not confused with a
            # normal Firestore array.
            cells.append(Cell(field_key(path), name, JSON, value))
        else:
            cells.append(Cell(field_key(path), name, JSON, value))
        return

    if isinstance(value, dict):
        if map_mode == "flatten" and value:
            for key, sub in value.items():
                _cell(path + (str(key),), sub, cells, arrays,
                      separator=separator, map_mode=map_mode, array_mode=array_mode)
        else:
            cells.append(Cell(field_key(path), name, JSON, value))
        return

    if isinstance(value, list):
        if array_mode == "table" and all(_is_scalar(v) for v in value):
            arrays.append(ArrayField(field_key(path), name, value))
            # Zero side-table rows otherwise cannot distinguish an empty array
            # from a field that was absent. The companion count preserves that
            # information while retaining normalised values/order in the child.
            cells.append(
                Cell(
                    field_key(path, "array_count"),
                    f"{name}{separator}count",
                    INT,
                    len(value),
                )
            )
        else:
            cells.append(Cell(field_key(path), name, JSON, value))
        return

    if value is None:
        cells.append(Cell(field_key(path), name, NULL, None))
    elif isinstance(value, bool):
        cells.append(Cell(field_key(path), name, BOOL, value))
    elif isinstance(value, int):
        cells.append(Cell(field_key(path), name, INT, value))
    elif isinstance(value, float):
        cells.append(Cell(field_key(path), name, FLOAT, value))
    else:
        cells.append(Cell(field_key(path), name, STRING, value if isinstance(value, str) else str(value)))


def flatten_document(
    data: dict,
    *,
    separator: str = "_",
    map_mode: str = "flatten",
    array_mode: str = "json",
) -> tuple[list[Cell], list[ArrayField]]:
    """Return structured cells and array fields for one Firestore document."""
    cells: list[Cell] = []
    arrays: list[ArrayField] = []
    for key, value in (data or {}).items():
        _cell((str(key),), value, cells, arrays,
              separator=separator, map_mode=map_mode, array_mode=array_mode)
    return cells, arrays


# ----------------------------------------------------------------------
# type reconciliation
# ----------------------------------------------------------------------
def reconcile(kinds: set[str]) -> str:
    real = {k for k in kinds if k != NULL}
    if not real:
        return STRING
    if len(real) == 1:
        return next(iter(real))
    if real == {INT, FLOAT}:
        return FLOAT
    if JSON in real:
        return JSON
    return STRING


# ----------------------------------------------------------------------
# schema model
# ----------------------------------------------------------------------
@dataclass
class Column:
    storage_key: Hashable
    source: str
    kinds: set = field(default_factory=set)
    present: int = 0
    sql_name: str = ""
    forced_kind: str | None = None
    is_meta: bool = False
    is_key: bool = False

    @property
    def kind(self) -> str:
        return self.forced_kind or reconcile(self.kinds)


@dataclass
class Table:
    key: Hashable
    label: str
    depth: int = 0
    parent_key: Hashable | None = None
    sql_name: str = ""
    columns: dict[Hashable, Column] = field(default_factory=dict)
    row_count: int = 0
    is_array_table: bool = False
    source_paths: set[str] = field(default_factory=set)
    all_nullable: bool = False

    def column(
        self,
        storage_key: Hashable,
        source: str,
        *,
        is_meta: bool = False,
    ) -> Column:
        col = self.columns.get(storage_key)
        if col is None:
            col = Column(storage_key=storage_key, source=source, is_meta=is_meta)
            self.columns[storage_key] = col
        elif is_meta:
            col.is_meta = True
        return col

    def observe(self, cell: Cell) -> None:
        col = self.column(cell.key, cell.name)
        col.kinds.add(cell.kind)
        col.present += 1

    @property
    def data_columns(self) -> list[Column]:
        return [c for c in self.columns.values() if not c.is_meta]

    def nullable(self, col: Column) -> bool:
        if col.is_meta:
            # Identity/path metadata is generated for every row in tables where
            # it exists. Only a normalised array value may legitimately be NULL.
            return col.source == "_value" and NULL in col.kinds
        if self.all_nullable:
            return True
        return NULL in col.kinds or col.present < self.row_count


# ----------------------------------------------------------------------
# identifier hygiene
# ----------------------------------------------------------------------
_BAD = re.compile(r"[^0-9a-zA-Z_]+")


def sanitize(
    name: str,
    used: set[str],
    *,
    max_len: int = 63,
    lowercase: bool = True,
    fallback: str = "field",
) -> str:
    text = _BAD.sub("_", str(name)).rstrip("_") or fallback
    if lowercase:
        text = text.lower()
    if text.strip("_") == "":
        text = fallback
    if text[0].isdigit():
        text = "f_" + text
    text = text[:max_len]

    candidate, suffix = text, 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = text[: max_len - len(tail)] + tail
        suffix += 1
    used.add(candidate)
    return candidate


# ----------------------------------------------------------------------
# builder
# ----------------------------------------------------------------------
class SchemaBuilder:
    def __init__(
        self,
        *,
        separator: str = "_",
        map_mode: str = "flatten",
        array_mode: str = "json",
        lowercase: bool = True,
        max_identifier: int = 63,
        table_prefix: str = "",
        all_nullable: bool = False,
    ) -> None:
        if not separator:
            raise ValueError("separator must not be empty")
        self.separator = separator
        self.map_mode = map_mode
        self.array_mode = array_mode
        self.lowercase = lowercase
        self.max_identifier = max_identifier
        self.table_prefix = table_prefix
        self.all_nullable = all_nullable
        self.tables: dict[Hashable, Table] = {}

    def _table(
        self,
        key: Hashable,
        label: str,
        depth: int = 0,
        parent_key: Hashable | None = None,
        is_array_table: bool = False,
    ) -> Table:
        table = self.tables.get(key)
        if table is None:
            table = Table(
                key=key,
                label=label,
                depth=depth,
                parent_key=parent_key,
                is_array_table=is_array_table,
                all_nullable=self.all_nullable,
            )
            self.tables[key] = table
        return table

    @staticmethod
    def _meta(table: Table, name: str, kind: str = STRING, *, is_key: bool = False) -> Column:
        col = table.column(meta_key(name), name, is_meta=True)
        col.forced_kind = kind
        col.is_key = is_key
        return col

    def observe(self, record) -> None:
        key = collection_key(record.collection)
        parent_key = parent_collection_key(record.collection) if record.depth > 0 else None
        table = self._table(key, collection_label(record.collection), record.depth, parent_key)
        table.row_count += 1
        table.source_paths.add(record.collection)

        self._meta(table, "_pk", is_key=True)
        self._meta(table, "_id")
        self._meta(table, "_path")
        if record.parent_path is not None:
            self._meta(table, "_parent_pk", is_key=True)
            self._meta(table, "_parent_id")
            self._meta(table, "_parent_path")

        cells, arrays = flatten_document(
            record.data,
            separator=self.separator,
            map_mode=self.map_mode,
            array_mode=self.array_mode,
        )
        for cell in cells:
            table.observe(cell)

        for array in arrays:
            child_key = array_key(key, array.key)
            child = self._table(
                child_key,
                f"{table.label}__{array.name}",
                record.depth + 1,
                key,
                is_array_table=True,
            )
            self._meta(child, "_parent_pk", is_key=True)
            self._meta(child, "_parent_id")
            self._meta(child, "_parent_path")
            self._meta(child, "_idx", INT, is_key=True)
            value_col = child.column(meta_key("_value"), "_value", is_meta=True)
            for item in array.values:
                child.row_count += 1
                sub_cells: list[Cell] = []
                _cell(("_value",), item, sub_cells, [], separator=self.separator,
                      map_mode="json", array_mode="json")
                # A scalar always yields one cell. Preserve its inferred kind in
                # the generated _value column while its storage key stays meta.
                if sub_cells:
                    value_col.kinds.add(sub_cells[0].kind)
                    value_col.present += 1

    def finalize(self) -> list[Table]:
        """Assign collision-free SQL identifiers and return parents first."""
        used_tables: set[str] = set()
        ordered = sorted(
            self.tables.values(),
            key=lambda t: (t.depth, t.label, t.is_array_table, repr(t.key)),
        )

        for table in ordered:
            table.sql_name = sanitize(
                f"{self.table_prefix}{table.label}",
                used_tables,
                max_len=self.max_identifier,
                lowercase=self.lowercase,
                fallback="collection",
            )

            used_cols: set[str] = set()
            # Generated columns get their exact names first. User fields with
            # the same text are then deterministically suffixed instead of lost.
            for name in META_NAMES:
                col = table.columns.get(meta_key(name))
                if col is not None:
                    col.sql_name = name
                    used_cols.add(name)

            user_columns = sorted(
                (col for col in table.columns.values() if not col.is_meta),
                key=lambda col: repr(col.storage_key),
            )
            for col in user_columns:
                if not col.sql_name:
                    col.sql_name = sanitize(
                        col.source,
                        used_cols,
                        max_len=self.max_identifier,
                        lowercase=self.lowercase,
                        fallback="field",
                    )

            # Rebuild the ordered mapping so generated DDL and INSERT column
            # order is reproducible even when Firestore map key order differs.
            ordered_columns = []
            for name in META_NAMES:
                col = table.columns.get(meta_key(name))
                if col is not None:
                    ordered_columns.append(col)
            ordered_columns.extend(user_columns)
            table.columns = {col.storage_key: col for col in ordered_columns}
        return ordered


def build_schema(records: Iterable, **kwargs) -> list[Table]:
    builder = SchemaBuilder(**kwargs)
    for record in records:
        builder.observe(record)
    return builder.finalize()
