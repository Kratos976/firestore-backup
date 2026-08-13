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

"""Render an inferred schema as SQL for PostgreSQL, MySQL/MariaDB or SQLite.

``generic`` is retained as conservative ANSI-oriented output for inspection or
manual adaptation. It is intentionally *not* advertised as guaranteed portable
across every engine; named dialects are the supported execution targets.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Any, Iterable

from .infer import (
    BOOL,
    BYTES,
    FLOAT,
    INT,
    JSON,
    NULL,
    REFERENCE,
    STRING,
    TIMESTAMP,
    Table,
    _cell,
    array_key,
    collection_key,
    document_pk,
    flatten_document,
    meta_key,
)


# ----------------------------------------------------------------------
# dialects
# ----------------------------------------------------------------------
class Dialect:
    name = "generic"
    quote_char = '"'
    identifier_limit = 63
    escape_backslash = False
    supports_table_if_not_exists = False
    supports_index_if_not_exists = False
    supports_add_constraint = True
    supports_ignore = False
    key_type = "VARCHAR(64)"
    types = {
        STRING: "TEXT",
        INT: "BIGINT",
        FLOAT: "DOUBLE PRECISION",
        BOOL: "BOOLEAN",
        TIMESTAMP: "TIMESTAMP",
        BYTES: "TEXT",
        REFERENCE: "TEXT",
        JSON: "TEXT",
        NULL: "TEXT",
    }
    keep_tz = False

    def quote(self, identifier: str) -> str:
        q = self.quote_char
        return f"{q}{identifier.replace(q, q + q)}{q}"

    def column_type(self, kind: str, is_key: bool = False) -> str:
        # Only textual identifiers need the bounded/indexable key type.
        if is_key and kind in (STRING, REFERENCE):
            return self.key_type
        return self.types.get(kind, "TEXT")

    def string(self, value: str) -> str:
        text = str(value)
        if "\x00" in text:
            raise ValueError(
                f"{self.name} TEXT output cannot safely preserve a NUL byte; "
                "refuse to generate lossy SQL"
            )
        if self.escape_backslash:
            text = text.replace("\\", "\\\\")
        return "'" + text.replace("'", "''") + "'"

    def boolean(self, value: bool) -> str:
        return "TRUE" if value else "FALSE"

    def number(self, value: Any) -> str:
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                return "NULL"
            return repr(value)
        return str(value)

    def timestamp(self, iso: str) -> str:
        try:
            moment = _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except ValueError:
            return self.string(iso)
        if self.keep_tz:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=_dt.timezone.utc)
            return self.string(moment.isoformat())
        if moment.tzinfo is not None:
            moment = moment.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return self.string(moment.strftime("%Y-%m-%d %H:%M:%S.%f"))

    def blob(self, b64: str) -> str:
        return self.string(b64)

    def json_value(self, value: Any) -> str:
        return self.string(json.dumps(value, ensure_ascii=False, sort_keys=False))

    def insert_prefix(self, on_conflict: bool) -> str:
        return "INSERT INTO"

    def insert_suffix(self, on_conflict: bool) -> str:
        return ""

    def begin(self) -> str:
        return "BEGIN;"

    def commit(self) -> str:
        return "COMMIT;"


class PostgresDialect(Dialect):
    name = "postgres"
    identifier_limit = 63
    keep_tz = True
    supports_table_if_not_exists = True
    supports_index_if_not_exists = True
    supports_ignore = True
    key_type = "CHAR(64)"
    types = {
        **Dialect.types,
        TIMESTAMP: "TIMESTAMPTZ",
        BYTES: "BYTEA",
        JSON: "JSONB",
    }

    def blob(self, b64: str) -> str:
        return f"decode({self.string(b64)}, 'base64')"

    def insert_suffix(self, on_conflict: bool) -> str:
        return " ON CONFLICT DO NOTHING" if on_conflict else ""


class MySQLDialect(Dialect):
    name = "mysql"
    quote_char = "`"
    identifier_limit = 64
    escape_backslash = True
    supports_table_if_not_exists = True
    supports_index_if_not_exists = False  # MySQL CREATE INDEX has no IF NOT EXISTS.
    supports_ignore = True
    key_type = "CHAR(64)"
    types = {
        **Dialect.types,
        STRING: "LONGTEXT",
        FLOAT: "DOUBLE",
        TIMESTAMP: "DATETIME(6)",
        BYTES: "LONGBLOB",
        JSON: "JSON",
    }

    def blob(self, b64: str) -> str:
        return f"FROM_BASE64({self.string(b64)})"

    def insert_prefix(self, on_conflict: bool) -> str:
        # INSERT IGNORE suppresses more than duplicate-key errors (including
        # data-conversion problems), which is unsafe for a migration tool.
        return "INSERT INTO"

    def begin(self) -> str:
        return "START TRANSACTION;"


class SQLiteDialect(Dialect):
    name = "sqlite"
    identifier_limit = 128
    supports_table_if_not_exists = True
    supports_index_if_not_exists = True
    supports_add_constraint = False
    supports_ignore = True
    key_type = "TEXT"
    types = {
        **Dialect.types,
        FLOAT: "REAL",
        INT: "INTEGER",
        TIMESTAMP: "TEXT",
        BYTES: "BLOB",
        JSON: "TEXT",
    }

    def blob(self, b64: str) -> str:
        import base64 as _b64

        try:
            return "X'" + _b64.b64decode(b64).hex() + "'"
        except Exception:
            return self.string(b64)

    def insert_suffix(self, on_conflict: bool) -> str:
        return " ON CONFLICT DO NOTHING" if on_conflict else ""


DIALECTS = {
    "generic": Dialect,
    "postgres": PostgresDialect,
    "postgresql": PostgresDialect,
    "mysql": MySQLDialect,
    "mariadb": MySQLDialect,
    "sqlite": SQLiteDialect,
}


def get_dialect(name: str) -> Dialect:
    try:
        return DIALECTS[name.lower()]()
    except KeyError:
        raise ValueError(
            f"Unknown dialect '{name}'. Choose from: {', '.join(sorted(set(DIALECTS)))}"
        ) from None


# ----------------------------------------------------------------------
# values
# ----------------------------------------------------------------------
def render(dialect: Dialect, kind: str, value: Any) -> str:
    if value is None:
        return "NULL"
    if kind == JSON:
        return dialect.json_value(value)
    if kind == TIMESTAMP:
        return dialect.timestamp(value)
    if kind == BYTES:
        return dialect.blob(value)
    if kind == BOOL:
        return dialect.boolean(bool(value))
    if kind == INT:
        if isinstance(value, bool):
            return dialect.number(int(value))
        if isinstance(value, (int, float)):
            return dialect.number(int(value))
        return dialect.string(value)
    if kind == FLOAT:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return dialect.number(float(value))
        return dialect.string(value)
    if isinstance(value, (dict, list)):
        return dialect.json_value(value)
    if isinstance(value, bool):
        return dialect.string("true" if value else "false")
    return dialect.string(value)




def _derived_identifier(base: str, limit: int) -> str:
    """Keep generated index/constraint names deterministic and collision-safe."""
    if len(base) <= limit:
        return base
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:10]
    head = base[: max(1, limit - len(digest) - 1)]
    return f"{head}_{digest}"

# ----------------------------------------------------------------------
# DDL
# ----------------------------------------------------------------------
def create_table(dialect: Dialect, table: Table) -> str:
    q = dialect.quote
    lines: list[str] = []

    for col in table.columns.values():
        sql_type = dialect.column_type(col.kind, is_key=col.is_key)
        parts = [f"  {q(col.sql_name)} {sql_type}"]
        if col.storage_key == meta_key("_pk") and not table.is_array_table:
            parts.append("PRIMARY KEY")
        elif not table.nullable(col):
            parts.append("NOT NULL")
        lines.append(" ".join(parts))

    if table.is_array_table:
        lines.append(f"  PRIMARY KEY ({q('_parent_pk')}, {q('_idx')})")

    exists = "IF NOT EXISTS " if dialect.supports_table_if_not_exists else ""
    return f"CREATE TABLE {exists}{q(table.sql_name)} (\n{',\n'.join(lines)}\n);"


def drop_table(dialect: Dialect, table: Table) -> str:
    suffix = " CASCADE" if dialect.name == "postgres" else ""
    return f"DROP TABLE IF EXISTS {dialect.quote(table.sql_name)}{suffix};"


def indexes(dialect: Dialect, table: Table) -> list[str]:
    q = dialect.quote
    out: list[str] = []
    if meta_key("_parent_pk") in table.columns and not table.is_array_table:
        name = _derived_identifier(
            f"idx_{table.sql_name}_parent", dialect.identifier_limit
        )
        exists = "IF NOT EXISTS " if dialect.supports_index_if_not_exists else ""
        out.append(
            f"CREATE INDEX {exists}{q(name)} "
            f"ON {q(table.sql_name)} ({q('_parent_pk')});"
        )
    return out


def foreign_keys(dialect: Dialect, tables: list[Table]) -> list[str]:
    q = dialect.quote
    if not dialect.supports_add_constraint:
        return []
    by_key = {t.key: t for t in tables}
    out: list[str] = []
    for table in tables:
        parent = by_key.get(table.parent_key)
        if parent is None or meta_key("_parent_pk") not in table.columns:
            continue
        name = _derived_identifier(
            f"fk_{table.sql_name}_parent", dialect.identifier_limit
        )
        out.append(
            f"ALTER TABLE {q(table.sql_name)} ADD CONSTRAINT {q(name)} "
            f"FOREIGN KEY ({q('_parent_pk')}) "
            f"REFERENCES {q(parent.sql_name)} ({q('_pk')});"
        )
    return out


# ----------------------------------------------------------------------
# rows
# ----------------------------------------------------------------------
def iter_rows(
    records: Iterable,
    tables: list[Table],
    *,
    separator: str,
    map_mode: str,
    array_mode: str,
):
    """Yield ``(table, ordered_values)`` for documents and array side tables."""
    by_key = {t.key: t for t in tables}

    for record in records:
        key = collection_key(record.collection)
        table = by_key.get(key)
        if table is None:
            continue

        cells, arrays = flatten_document(
            record.data,
            separator=separator,
            map_mode=map_mode,
            array_mode=array_mode,
        )
        values: dict[Any, Any] = {cell.key: cell.value for cell in cells}
        values[meta_key("_pk")] = document_pk(record.path)
        values[meta_key("_id")] = record.id
        values[meta_key("_path")] = record.path
        if record.parent_path is not None:
            values[meta_key("_parent_pk")] = document_pk(record.parent_path)
            values[meta_key("_parent_id")] = record.parent_id
            values[meta_key("_parent_path")] = record.parent_path

        yield table, [values.get(col.storage_key) for col in table.columns.values()]

        for array in arrays:
            child = by_key.get(array_key(key, array.key))
            if child is None:
                continue
            for index, item in enumerate(array.values):
                sub_cells = []
                _cell(("_value",), item, sub_cells, [], separator=separator,
                      map_mode="json", array_mode="json")
                value = sub_cells[0].value if sub_cells else None
                sub_values = {
                    meta_key("_parent_pk"): document_pk(record.path),
                    meta_key("_parent_id"): record.id,
                    meta_key("_parent_path"): record.path,
                    meta_key("_idx"): index,
                    meta_key("_value"): value,
                }
                yield child, [sub_values.get(c.storage_key) for c in child.columns.values()]


def insert_statements(
    dialect: Dialect,
    table: Table,
    rows: list,
    batch_size: int = 200,
    on_conflict: bool = False,
):
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    q = dialect.quote
    columns = list(table.columns.values())
    column_sql = ", ".join(q(c.sql_name) for c in columns)
    ignore = on_conflict and dialect.supports_ignore
    verb = dialect.insert_prefix(ignore)
    if ignore and dialect.name == "mysql":
        pk_name = "_parent_pk" if table.is_array_table else "_pk"
        suffix = f" ON DUPLICATE KEY UPDATE {q(pk_name)} = {q(pk_name)}"
    else:
        suffix = dialect.insert_suffix(ignore)

    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        rendered = []
        for row in chunk:
            literals = ", ".join(
                render(dialect, col.kind, value) for col, value in zip(columns, row)
            )
            rendered.append(f"  ({literals})")
        body = ",\n".join(rendered)
        yield f"{verb} {q(table.sql_name)} ({column_sql}) VALUES\n{body}{suffix};"
