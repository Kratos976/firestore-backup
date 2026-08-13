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

"""Authentication and Firestore client construction.

Credentials are resolved in this order:

1. ``--credentials <file>``          service-account JSON key
2. ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable
3. Application Default Credentials  (``gcloud auth application-default login``)
4. Interactive prompt               (when running the wizard on a TTY)

Setting ``FIRESTORE_EMULATOR_HOST`` (or ``--emulator``) bypasses auth entirely
and talks to a local emulator - handy for a dry run before touching production.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass


class AuthError(RuntimeError):
    pass


@dataclass
class Credentials:
    credentials_path: str | None = None
    project_id: str | None = None
    database: str = "(default)"
    emulator_host: str | None = None


def read_project_from_key(path: str) -> str | None:
    """Pull ``project_id`` out of a service-account key file."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("project_id")
    except Exception:
        return None


def prompt_credentials(defaults: Credentials | None = None) -> Credentials:
    """Ask the user for whatever is needed to authenticate."""
    d = defaults or Credentials()

    if not sys.stdin.isatty():
        raise AuthError(
            "No credentials supplied and stdin is not a terminal. "
            "Use --credentials, or set GOOGLE_APPLICATION_CREDENTIALS."
        )

    env_key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    suggestion = d.credentials_path or env_key or ""
    hint = f" [{suggestion}]" if suggestion else " (blank = use gcloud ADC)"

    print("\n-- Firebase / Firestore credentials --")
    print("   Service account key: Firebase console > Project settings >")
    print("   Service accounts > Generate new private key\n")

    path = input(f"Path to service-account JSON{hint}: ").strip() or suggestion
    path = os.path.expanduser(path) if path else None
    if path and not os.path.isfile(path):
        raise AuthError(f"File not found: {path}")

    detected = read_project_from_key(path) if path else None
    proj_hint = f" [{detected}]" if detected else ""
    project = input(f"Project ID{proj_hint}: ").strip() or detected
    if not project and not path:
        raise AuthError("A project ID is required when not using a key file.")

    database = input('Database ID [(default)]: ').strip() or "(default)"

    return Credentials(
        credentials_path=path,
        project_id=project,
        database=database,
        emulator_host=d.emulator_host,
    )


def build_client(creds: Credentials):
    """Return an authenticated ``google.cloud.firestore.Client``."""
    try:
        from google.cloud import firestore  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise AuthError(
            "google-cloud-firestore is not installed.\n"
            "    pip install -r requirements.txt"
        ) from exc

    if creds.emulator_host:
        os.environ["FIRESTORE_EMULATOR_HOST"] = creds.emulator_host

    kwargs = {}
    project = creds.project_id
    if creds.credentials_path:
        try:
            from google.oauth2 import service_account  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AuthError("google-auth is not installed.") from exc
        try:
            kwargs["credentials"] = service_account.Credentials.from_service_account_file(
                creds.credentials_path
            )
        except Exception as exc:
            raise AuthError(
                f"Could not read service-account credentials {creds.credentials_path!r}: {exc}"
            ) from exc
        project = project or read_project_from_key(creds.credentials_path)

    if project:
        kwargs["project"] = project
    if creds.database and creds.database != "(default)":
        kwargs["database"] = creds.database

    try:
        return firestore.Client(**kwargs)
    except Exception as exc:
        raise AuthError(f"Could not create the Firestore client: {exc}") from exc
