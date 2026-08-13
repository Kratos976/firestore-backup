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

"""Command-line interface for Firestore -> JSON -> SQL."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import signal
import sys
import tempfile

from . import __version__
from .client import AuthError, Credentials, build_client, prompt_credentials, read_project_from_key
from .export import FirestoreExporter, records_to_tree
from .generate import describe, generate_sql
from .infer import SchemaBuilder
from .loader import iter_records
from .sql import DIALECTS


def log(message: str = "") -> None:
    print(message, file=sys.stderr, flush=True)


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _depth_int(text: str) -> int:
    value = _non_negative_int(text)
    if value > 100:
        raise argparse.ArgumentTypeError("must be <= 100 (Firestore subcollection limit)")
    return value


def _non_empty(text: str) -> str:
    if text == "":
        raise argparse.ArgumentTypeError("must not be empty")
    return text


def _read_time(text: str) -> _dt.datetime:
    try:
        value = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an RFC 3339 / ISO-8601 timestamp") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc)


def _temporary_output(final_path: str) -> tuple[str, object]:
    """Open a temporary sibling file suitable for an atomic ``os.replace``."""
    final_abs = os.path.abspath(final_path)
    directory = os.path.dirname(final_abs) or os.getcwd()
    os.makedirs(directory, exist_ok=True)
    prefix = f".{os.path.basename(final_abs)}."
    fd, temp_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory, text=True)
    return temp_path, os.fdopen(fd, "w", encoding="utf-8", newline="\n")


def _sync_close(fh) -> None:
    fh.flush()
    os.fsync(fh.fileno())
    fh.close()


def _remove_quietly(path: str | None) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _same_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    a = os.path.normcase(os.path.realpath(os.path.abspath(left)))
    b = os.path.normcase(os.path.realpath(os.path.abspath(right)))
    if a == b:
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _replace_atomic(temp_path: str, final_path: str) -> None:
    """Atomically replace *final_path* and fsync its directory where supported."""
    os.replace(temp_path, final_path)
    directory = os.path.dirname(os.path.abspath(final_path)) or os.getcwd()
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(directory, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        # Directory fsync is not available on every platform/filesystem. The
        # file itself has already been fsynced before os.replace.
        pass


# ----------------------------------------------------------------------
# phase 1
# ----------------------------------------------------------------------
def run_export(args) -> int:
    creds = Credentials(
        credentials_path=args.credentials,
        project_id=args.project,
        database=args.database,
        emulator_host=args.emulator,
    )

    env_credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    for secret_path in (creds.credentials_path, env_credentials):
        if _same_path(args.output, secret_path):
            log("error: export output must not overwrite the credential file")
            return 2

    if args.no_subcollections and args.subcollection_names:
        log("error: --no-subcollections cannot be combined with --subcollection-names")
        return 2

    if (
        not creds.credentials_path
        and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        and not creds.emulator_host
        and not args.adc
    ):
        try:
            creds = prompt_credentials(creds)
        except AuthError as exc:
            log(f"error: {exc}")
            return 2

    for secret_path in (creds.credentials_path, os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")):
        if _same_path(args.output, secret_path):
            log("error: export output must not overwrite the credential file")
            return 2

    if creds.credentials_path and not creds.project_id:
        creds.project_id = read_project_from_key(creds.credentials_path)

    log(f"\nProject:  {creds.project_id or '(from environment)'}")
    log(f"Database: {creds.database}")
    log(f"Output:   {args.output}  [{args.format}]\n")

    try:
        client = build_client(creds)
    except AuthError as exc:
        log(f"error: {exc}")
        return 2

    read_time = args.read_time
    if args.snapshot:
        read_time = _dt.datetime.now(_dt.timezone.utc)

    exporter = FirestoreExporter(
        client,
        include_subcollections=not args.no_subcollections,
        subcollection_names=args.subcollection_names,
        collection_group_names=args.collection_groups,
        read_time=read_time,
        max_depth=args.max_depth,
        doc_limit=args.limit,
        page_size=args.page_size,
        on_progress=lambda message, _stats: log(message),
    )

    meta = {
        "dump_schema": 1,
        "generator": f"firestore-backup {__version__}",
        "exported_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "project_id": creds.project_id,
        "database": creds.database,
        "format": args.format,
    }
    if args.collection_groups:
        meta["collection_groups"] = args.collection_groups
    if read_time is not None:
        meta["read_time"] = read_time.isoformat()

    temp_path = None
    fh = None
    log("Reading Firestore...")
    try:
        temp_path, fh = _temporary_output(args.output)
        if args.format == "ndjson":
            fh.write(
                json.dumps({"type": "meta", **meta, "complete": False}, ensure_ascii=False) + "\n"
            )
            for record in exporter.walk(args.collections):
                fh.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
            # A final meta line makes record/error counts available without
            # buffering the whole export or rewriting the first line.
            fh.write(
                json.dumps(
                    {
                        "type": "meta",
                        **meta,
                        "final": True,
                        "complete": exporter.stats.errors == 0,
                        "documents": exporter.stats.documents,
                        "collections": exporter.stats.collections,
                        "errors": exporter.stats.errors,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        else:
            tree = records_to_tree(exporter.walk(args.collections))
            meta["documents"] = exporter.stats.documents
            meta["collections_count"] = exporter.stats.collections
            meta["errors"] = exporter.stats.errors
            meta["complete"] = exporter.stats.errors == 0
            json.dump(
                {"__meta__": meta, "collections": tree},
                fh,
                ensure_ascii=False,
                indent=None if args.compact else 2,
            )
            fh.write("\n")

        _sync_close(fh)
        fh = None

        if exporter.stats.errors:
            raise RuntimeError(
                f"export incomplete: {exporter.stats.errors} read/discovery error(s); "
                "existing output was left untouched"
            )

        _replace_atomic(temp_path, args.output)
        temp_path = None
    except KeyboardInterrupt:
        if fh is not None and not fh.closed:
            fh.close()
        _remove_quietly(temp_path)
        log("\ninterrupted - existing output was left untouched")
        return 130
    except Exception as exc:
        if fh is not None and not fh.closed:
            fh.close()
        _remove_quietly(temp_path)
        log(f"error during export: {exc}")
        return 1

    stats = exporter.stats
    size_mb = os.path.getsize(args.output) / (1024 * 1024)
    log(
        f"\nDone: {stats.documents} documents from {stats.collections} collections "
        f"in {stats.elapsed:.1f}s ({size_mb:.1f} MB)"
    )
    log(f"\nNext: python -m firestore_backup sql {args.output} -o schema.sql --dialect postgres")
    return 0


# ----------------------------------------------------------------------
# phase 2
# ----------------------------------------------------------------------
def run_sql(args) -> int:
    if not os.path.isfile(args.dump):
        log(f"error: no such file: {args.dump}")
        return 2

    schema_path = args.output
    data_path = args.data_output
    if _same_path(schema_path, args.dump) or (data_path and _same_path(data_path, args.dump)):
        log("error: SQL output must not overwrite the source dump")
        return 2
    if data_path and _same_path(data_path, schema_path):
        log("error: --output and --data-output must be different files")
        return 2

    schema_tmp = data_tmp = None
    schema_fh = data_fh = None
    try:
        schema_tmp, schema_fh = _temporary_output(schema_path)
        if data_path:
            data_tmp, data_fh = _temporary_output(data_path)
            generate_sql(
                args.dump,
                schema_fh,
                data_fh,
                **_sql_options(args),
                on_progress=log,
            )
            _sync_close(data_fh)
            data_fh = None
        else:
            generate_sql(
                args.dump,
                schema_fh,
                None,
                **_sql_options(args),
                on_progress=log,
            )

        _sync_close(schema_fh)
        schema_fh = None

        # Replace only after every generation step succeeded.
        _replace_atomic(schema_tmp, schema_path)
        schema_tmp = None
        if data_path:
            _replace_atomic(data_tmp, data_path)
            data_tmp = None
    except Exception as exc:
        for open_fh in (schema_fh, data_fh):
            if open_fh is not None and not open_fh.closed:
                open_fh.close()
        _remove_quietly(schema_tmp)
        _remove_quietly(data_tmp)
        log(f"error during SQL generation: {exc}")
        return 1

    log(f"\nWritten: {schema_path}" + (f" and {data_path}" if data_path else ""))
    return 0


def _sql_options(args) -> dict:
    return dict(
        dialect=args.dialect,
        separator=args.separator,
        map_mode=args.maps,
        array_mode=args.arrays,
        lowercase=not args.preserve_case,
        table_prefix=args.table_prefix,
        batch_size=args.batch_size,
        drop=args.drop,
        transaction=not args.no_transaction,
        add_foreign_keys=not args.no_foreign_keys,
        on_conflict=args.skip_existing,
        all_nullable=args.all_nullable,
        fmt=args.input_format,
    )


def run_inspect(args) -> int:
    if not os.path.isfile(args.dump):
        log(f"error: no such file: {args.dump}")
        return 2

    try:
        builder = SchemaBuilder(
            separator=args.separator,
            map_mode=args.maps,
            array_mode=args.arrays,
            lowercase=not args.preserve_case,
            max_identifier=DIALECTS[args.dialect]().identifier_limit,
            all_nullable=args.all_nullable,
        )
        count = 0
        for record in iter_records(args.dump, args.input_format):
            builder.observe(record)
            count += 1
        tables = builder.finalize()
    except Exception as exc:
        log(f"error during inspection: {exc}")
        return 1

    print(f"{count} documents -> {len(tables)} tables   (* = generated column)")
    for line in describe(tables):
        print(line)
    return 0


# ----------------------------------------------------------------------
# wizard
# ----------------------------------------------------------------------
def run_wizard(parser) -> int:
    print("firestore-backup - Firestore export and SQL conversion\n")
    print("  1) Export Firestore to JSON        (phase 1)")
    print("  2) Convert an existing JSON to SQL (phase 2)")
    print("  3) Inspect a dump's inferred schema")
    choice = input("\nChoice [1]: ").strip() or "1"

    if choice == "1":
        output = input("Output file [firestore-dump.json]: ").strip() or "firestore-dump.json"
        fmt = "ndjson" if output.endswith((".ndjson", ".jsonl")) else "json"
        return run_export(parser.parse_args(["export", "-o", output, "--format", fmt]))

    if choice == "2":
        dump = input("Dump file [firestore-dump.json]: ").strip() or "firestore-dump.json"
        print("Dialects: postgres, mysql/mariadb, sqlite")
        dialect = input("Dialect [postgres]: ").strip() or "postgres"
        output = input("Output .sql [schema.sql]: ").strip() or "schema.sql"
        return run_sql(parser.parse_args(["sql", dump, "-o", output, "--dialect", dialect]))

    if choice == "3":
        dump = input("Dump file [firestore-dump.json]: ").strip() or "firestore-dump.json"
        return run_inspect(parser.parse_args(["inspect", dump]))

    log("nothing to do")
    return 0


# ----------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="firestore-backup",
        description="Export a Firestore database to JSON, then convert it to SQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m firestore_backup export --credentials key.json -o dump.json\n"
            "  python -m firestore_backup export --format ndjson -o dump.ndjson\n"
            "  python -m firestore_backup inspect dump.json --dialect postgres\n"
            "  python -m firestore_backup sql dump.json -o schema.sql --dialect postgres\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"firestore-backup {__version__}")
    sub = parser.add_subparsers(dest="command")

    export = sub.add_parser("export", help="phase 1: Firestore -> JSON")
    export.add_argument("-c", "--credentials", help="service-account JSON key file")
    export.add_argument("-p", "--project", help="GCP project id")
    export.add_argument("--database", type=_non_empty, default="(default)", help="Firestore database id")
    export.add_argument("--emulator", help="host:port of a local Firestore emulator")
    export.add_argument("--adc", action="store_true", help="use Application Default Credentials, do not prompt")
    export.add_argument("-o", "--output", default="firestore-dump.json")
    export.add_argument("--format", choices=["json", "ndjson"], default="json",
                        help="ndjson streams to disk and uses constant memory")
    export.add_argument("--compact", action="store_true", help="no indentation")
    export.add_argument("--collections", nargs="+", help="only these root collections (default: all)")
    export.add_argument("--no-subcollections", action="store_true")
    export.add_argument(
        "--subcollection-names",
        nargs="+",
        help="probe only these child collection names under each reached document",
    )
    export.add_argument(
        "--collection-groups",
        nargs="+",
        help="also query these collection IDs globally; use this to include known orphan subcollections",
    )
    consistency = export.add_mutually_exclusive_group()
    consistency.add_argument(
        "--snapshot",
        action="store_true",
        help="use one fixed Firestore read_time for a point-in-time export (must complete within the read_time window)",
    )
    consistency.add_argument(
        "--read-time",
        type=_read_time,
        help="read all queries at this RFC3339 timestamp; older timestamps require Firestore PITR",
    )
    export.add_argument(
        "--max-depth",
        type=_depth_int,
        default=100,
        help="maximum subcollection depth (default: 100, Firestore maximum)",
    )
    export.add_argument("--limit", type=_positive_int, help="max documents per collection/query (testing)")
    export.add_argument("--page-size", type=_positive_int, default=300)

    sql = sub.add_parser("sql", help="phase 2: JSON -> SQL")
    sql.add_argument("dump", help="dump file produced by 'export'")
    sql.add_argument("-o", "--output", default="schema.sql")
    sql.add_argument("--data-output", help="write INSERTs to a separate file")
    sql.add_argument("--dialect", default="postgres", choices=sorted(set(DIALECTS)))
    sql.add_argument("--drop", action="store_true", help="emit DROP TABLE first")
    sql.add_argument("--skip-existing", action="store_true", help="ignore rows whose primary key already exists")
    sql.add_argument("--no-transaction", action="store_true")
    sql.add_argument("--no-foreign-keys", action="store_true")
    sql.add_argument("--batch-size", type=_positive_int, default=200)
    sql.add_argument("--table-prefix", default="")

    inspect = sub.add_parser("inspect", help="print the inferred schema, write nothing")
    inspect.add_argument("dump")
    inspect.add_argument("--dialect", default="postgres", choices=sorted(set(DIALECTS)))

    for sub_parser in (sql, inspect):
        sub_parser.add_argument("--maps", choices=["flatten", "json"], default="flatten",
                                help="nested maps: flatten into columns, or keep as JSON")
        sub_parser.add_argument("--arrays", choices=["json", "table"], default="json",
                                help="arrays: JSON column, or a normalised side table")
        sub_parser.add_argument("--separator", type=_non_empty, default="_",
                                help="separator for flattened SQL field names")
        sub_parser.add_argument("--preserve-case", action="store_true")
        sub_parser.add_argument("--all-nullable", action="store_true",
                                help="emit no NOT NULL for data fields (use with sampled dumps)")
        sub_parser.add_argument("--input-format", choices=["json", "ndjson"], help="override format detection")
    return parser


def main(argv=None) -> int:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        if sys.stdin.isatty():
            try:
                return run_wizard(parser)
            except (KeyboardInterrupt, EOFError):
                log("\naborted")
                return 130
        parser.print_help()
        return 0

    if args.command == "export":
        return run_export(args)
    if args.command == "sql":
        return run_sql(args)
    if args.command == "inspect":
        return run_inspect(args)

    parser.print_help()
    return 0
