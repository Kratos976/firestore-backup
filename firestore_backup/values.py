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

"""Lossless JSON representation of Firestore values.

Firestore has values that JSON cannot represent directly: timestamps,
GeoPoints, references, bytes and NaN/Infinity. They are encoded as small typed
objects. A normal user map that itself contains ``__fs_type__`` is wrapped as a
``map`` envelope, so it can never be mistaken for one of our internal types.

The top-level document object is encoded with :func:`encode_document`; its
field names are never interpreted as envelope metadata.
"""

from __future__ import annotations

import base64
import datetime as _dt
import math
from typing import Any

TYPE_KEY = "__fs_type__"

T_TIMESTAMP = "timestamp"
T_GEOPOINT = "geopoint"
T_REFERENCE = "reference"
T_BYTES = "bytes"
T_DOUBLE = "double"
T_MAP = "map"
T_VECTOR = "vector"

KNOWN_TYPES = {T_TIMESTAMP, T_GEOPOINT, T_REFERENCE, T_BYTES, T_DOUBLE, T_MAP, T_VECTOR}


def _class_name_in(value: Any, *names: str) -> bool:
    return type(value).__name__ in names


def _iso(value: _dt.datetime) -> str:
    rfc = getattr(value, "rfc3339", None)
    if callable(rfc):
        try:
            return rfc()
        except Exception:  # pragma: no cover - defensive
            pass
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return value.isoformat()


def is_envelope(value: Any) -> bool:
    """Return True only for an envelope created by this package."""
    return (
        isinstance(value, dict)
        and isinstance(value.get(TYPE_KEY), str)
        and value.get(TYPE_KEY) in KNOWN_TYPES
    )


def envelope_type(value: Any) -> str | None:
    return value[TYPE_KEY] if is_envelope(value) else None


def encode_document(data: dict | None) -> dict:
    """Encode one Firestore document while preserving its top-level keys."""
    return {str(k): encode(v) for k, v in (data or {}).items()}


def encode(value: Any) -> Any:
    """Convert a Firestore SDK value into JSON-safe, reversible data."""
    if value is None or isinstance(value, (str, bool)):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if math.isnan(value):
            return {TYPE_KEY: T_DOUBLE, "value": "NaN"}
        if math.isinf(value):
            return {TYPE_KEY: T_DOUBLE, "value": "Infinity" if value > 0 else "-Infinity"}
        return value

    if isinstance(value, (bytes, bytearray, memoryview)):
        return {TYPE_KEY: T_BYTES, "value": base64.b64encode(bytes(value)).decode("ascii")}

    if isinstance(value, _dt.datetime):
        return {TYPE_KEY: T_TIMESTAMP, "value": _iso(value)}

    if isinstance(value, _dt.date):
        midnight = _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
        return {TYPE_KEY: T_TIMESTAMP, "value": midnight.isoformat()}

    if _class_name_in(value, "GeoPoint"):
        return {
            TYPE_KEY: T_GEOPOINT,
            "latitude": float(value.latitude),
            "longitude": float(value.longitude),
        }

    if _class_name_in(value, "DocumentReference", "AsyncDocumentReference"):
        path = getattr(value, "path", None)
        if path is None:
            path = "/".join(getattr(value, "_path", ()) or ())
        return {TYPE_KEY: T_REFERENCE, "path": path}

    if _class_name_in(value, "Vector"):
        try:
            return {TYPE_KEY: T_VECTOR, "value": [float(x) for x in value.value]}
        except Exception as exc:
            raise TypeError("could not serialise Firestore Vector value") from exc

    if isinstance(value, dict):
        mapped = {str(k): encode(v) for k, v in value.items()}
        # Escape any user map whose shape could otherwise be interpreted as an
        # internal typed envelope. The wrapper itself is unambiguous because
        # its value is decoded as map contents rather than as a standalone map.
        if isinstance(mapped.get(TYPE_KEY), str):
            return {TYPE_KEY: T_MAP, "value": mapped}
        return mapped

    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]

    # A backup must never silently stringify a future/unknown Firestore value.
    # Failing here leaves the previous atomic output untouched and makes the
    # unsupported type visible to the caller instead of corrupting semantics.
    raise TypeError(
        f"unsupported Firestore value type: {type(value).__module__}.{type(value).__qualname__}"
    )


def _decode_map_contents(mapped: dict, client: Any = None) -> dict:
    return {k: decode(v, client) for k, v in mapped.items()}


def decode(value: Any, client: Any = None) -> Any:
    """Inverse of :func:`encode` for supported Firestore-native types."""
    if is_envelope(value):
        kind = envelope_type(value)
        if kind == T_TIMESTAMP:
            return _dt.datetime.fromisoformat(str(value["value"]).replace("Z", "+00:00"))
        if kind == T_BYTES:
            return base64.b64decode(value["value"])
        if kind == T_DOUBLE:
            return float(value["value"])
        if kind == T_GEOPOINT:
            try:
                from google.cloud.firestore_v1 import GeoPoint  # type: ignore

                return GeoPoint(value["latitude"], value["longitude"])
            except Exception:
                return {"latitude": value["latitude"], "longitude": value["longitude"]}
        if kind == T_REFERENCE:
            if client is not None:
                return client.document(value["path"])
            return value["path"]
        if kind == T_MAP:
            mapped = value.get("value") or {}
            return _decode_map_contents(mapped, client)
        if kind == T_VECTOR:
            values = [float(x) for x in value.get("value", [])]
            try:
                from google.cloud.firestore_v1.vector import Vector  # type: ignore

                return Vector(values)
            except Exception:
                return values

    if isinstance(value, dict):
        return _decode_map_contents(value, client)
    if isinstance(value, list):
        return [decode(v, client) for v in value]
    return value
