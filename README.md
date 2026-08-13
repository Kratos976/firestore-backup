# firestore-backup

Export your Firestore database to JSON or NDJSON, then convert that dump into SQL for PostgreSQL, MySQL/MariaDB or SQLite.

```text
Firestore
    │
    ▼
JSON / NDJSON
    │
    ├── PostgreSQL
    ├── MySQL / MariaDB
    └── SQLite
```

Everything runs locally: **your Firestore credentials and database contents stay on your machine.**

---

# Getting started

You need:

1. **Python 3.9–3.14**
2. this package installed
3. a Firestore service-account JSON file with read access to the database

If you already have these three things, you can make your first export in a few minutes.

---

## 1. Install Python

Download and install Python from:

https://www.python.org/downloads/

On Windows, make sure Python is available from PowerShell.

Check with:

```powershell
python --version
```

You should see something similar to:

```text
Python 3.14.x
```

---

## 2. Install firestore-backup

Once the package is available on PyPI:

```powershell
python -m pip install firestore-backup
```

If you downloaded or cloned the source code instead, open PowerShell inside the project directory and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e .
```

On macOS or Linux, activate the virtual environment with:

```bash
source .venv/bin/activate
```

Check that the installation works:

```powershell
firestore-backup --version
```

You can also see all available commands with:

```powershell
firestore-backup --help
```

---

## 3. Get your Firestore credentials

To read a real Firestore database, you need a Google service-account JSON key.

The file usually looks like this:

```text
my-project-service-account.json
```

It contains private credentials, so **never commit it to GitHub and never place it inside a public repository**.

A good place on Windows is:

```text
C:\Users\YOUR_NAME\.secrets\firestore\my-project.json
```

For example:

```text
C:\Users\Feder\.secrets\firestore\my-project.json
```

The service account only needs permission to read Firestore. A read-only role such as:

```text
roles/datastore.viewer
```

is preferable when it fits your project.

### Where do I get the JSON key?

In Google Cloud Console:

1. open the Google Cloud project containing your Firestore database;
2. go to **IAM & Admin → Service Accounts**;
3. create or select a service account;
4. give it read access to Firestore;
5. open **Keys**;
6. create a new **JSON** key;
7. save the downloaded file somewhere outside the repository.

Keep that file private.

---

## 4. Make a small test export first

Do **not** start with the entire production database.

First export a small sample:

```powershell
firestore-backup export `
  --credentials "C:\Users\YOUR_NAME\.secrets\firestore\my-project.json" `
  --limit 50 `
  --no-subcollections `
  -o "out\probe.json"
```

Example:

```powershell
firestore-backup export `
  --credentials "C:\Users\Feder\.secrets\firestore\my-project.json" `
  --limit 50 `
  --no-subcollections `
  -o "out\probe.json"
```

If the command succeeds, you should now have:

```text
out\probe.json
```

That file contains the exported Firestore documents.

Open it with a text editor if you want to inspect the result.

---

## 5. Export the complete database

Once the small test looks correct:

```powershell
firestore-backup export `
  --credentials "C:\Users\YOUR_NAME\.secrets\firestore\my-project.json" `
  -o "out\dump.json"
```

For larger databases, NDJSON is recommended:

```powershell
firestore-backup export `
  --credentials "C:\Users\YOUR_NAME\.secrets\firestore\my-project.json" `
  --format ndjson `
  -o "out\dump.ndjson"
```

Use:

* **JSON** for smaller databases and easier manual inspection;
* **NDJSON** for larger databases and lower memory usage.

---

## 6. Inspect the SQL schema

Before generating the SQL file, preview what will be created.

For PostgreSQL:

```powershell
firestore-backup inspect out\dump.json --dialect postgres
```

For MySQL:

```powershell
firestore-backup inspect out\dump.json --dialect mysql
```

For SQLite:

```powershell
firestore-backup inspect out\dump.json --dialect sqlite
```

---

## 7. Generate the SQL file

### PostgreSQL

```powershell
firestore-backup sql `
  out\dump.json `
  -o "out\migration.sql" `
  --dialect postgres
```

### MySQL / MariaDB

```powershell
firestore-backup sql `
  out\dump.json `
  -o "out\migration.sql" `
  --dialect mysql
```

### SQLite

```powershell
firestore-backup sql `
  out\dump.json `
  -o "out\migration.sql" `
  --dialect sqlite
```

You now have:

```text
Firestore
    ↓
out\dump.json
    ↓
out\migration.sql
```

The JSON/NDJSON dump should be kept as the main exported copy.

The SQL file can be regenerated later with another SQL dialect or different mapping options without reconnecting to Firestore.

---

## The complete basic workflow

For a new user, the entire process is essentially:

```text
1. Install Python
2. Install firestore-backup
3. Download a read-only Firestore service-account key
4. Keep the key outside the repository
5. Run a small export with --limit
6. Run the complete export
7. Inspect the inferred SQL schema
8. Generate PostgreSQL, MySQL or SQLite SQL
```

On Windows, the minimum real-world example is:

```powershell
firestore-backup export `
  --credentials "C:\Users\Feder\.secrets\firestore\my-project.json" `
  --limit 50 `
  -o "out\probe.json"

firestore-backup inspect out\probe.json --dialect postgres

firestore-backup sql `
  out\probe.json `
  -o "out\migration.sql" `
  --dialect postgres
```

If these commands work, the basic setup is complete.

---

## Other authentication methods

A service-account JSON file is the simplest method to understand, but it is not the only supported option.

### Application Default Credentials

```powershell
firestore-backup export `
  --adc `
  --project "my-project" `
  -o "out\dump.json"
```

### `GOOGLE_APPLICATION_CREDENTIALS`

PowerShell:

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\secure\key.json"

firestore-backup export `
  --project "my-project" `
  -o "out\dump.json"
```

### Firestore emulator

```powershell
firestore-backup export `
  --emulator "localhost:8080" `
  --project "demo" `
  -o "out\demo.json"
```

If you are unsure which method to use, start with an explicit service-account JSON file. It is the easiest setup to understand and troubleshoot.

---

# Why two phases?

Firestore access and SQL generation are intentionally separated.

The first phase needs your Firestore credentials:

```text
Firestore → JSON / NDJSON
```

The second phase does not:

```text
JSON / NDJSON → SQL
```

Once the dump exists, you can:

* inspect it;
* archive it;
* generate PostgreSQL SQL;
* later regenerate MySQL or SQLite SQL;
* change mapping options;
* work completely offline.

You do not need to reconnect to Firestore every time.

---

# Main features

* Firestore → JSON export.
* Streaming Firestore → NDJSON export for larger databases.
* JSON / NDJSON → PostgreSQL SQL.
* JSON / NDJSON → MySQL / MariaDB SQL.
* JSON / NDJSON → SQLite SQL.
* Recursive subcollection traversal.
* Support for known orphan subcollections through collection-group queries.
* Optional fixed `read_time` exports.
* Firestore timestamps, GeoPoints, references, bytes and vectors preserved explicitly.
* Collision-safe mapping of document IDs, paths, flattened fields and table names.
* Atomic output writes.
* Detection of truncated versioned dumps.
* SQL schema preview before generation.
* No Firestore credentials required during SQL generation.

---

# Credentials and privacy

Credentials are runtime inputs.

**Do not edit the source code to insert credentials, project IDs or private keys.**

Supported authentication methods include:

### Service-account file

```bash
firestore-backup export \
  --credentials /secure/path/key.json \
  -o out/dump.json
```

### Application Default Credentials

```bash
firestore-backup export \
  --adc \
  --project my-project \
  -o out/dump.json
```

### `GOOGLE_APPLICATION_CREDENTIALS`

macOS / Linux:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/secure/path/key.json
firestore-backup export --project my-project -o out/dump.json
```

PowerShell:

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\secure\key.json"

firestore-backup export `
  --project "my-project" `
  -o "out\dump.json"
```

### Firestore emulator

```bash
firestore-backup export \
  --emulator localhost:8080 \
  --project demo \
  -o out/demo.json
```

For exports, prefer a read-only Firestore role such as:

```text
roles/datastore.viewer
```

when that permission model fits your project.

## Keep credentials outside the repository

Prefer:

```text
C:\Users\you\.secrets\firestore\key.json
```

instead of:

```text
your-repository\key.json
```

Never commit:

* service-account keys;
* `.env` files containing secrets;
* real production exports;
* database dumps containing sensitive data.

The bundled `.gitignore` blocks common credential and dump patterns, but keeping secrets physically outside the repository is safer.

---

# JSON or NDJSON?

Both formats contain the exported Firestore data.

## JSON

Use JSON when:

* the database is small or medium;
* you want an easy-to-read nested file;
* you want to inspect the dump manually.

Example:

```bash
firestore-backup export \
  -c key.json \
  -o out/dump.json
```

JSON builds the exported structure in memory before writing it.

## NDJSON

Use NDJSON when:

* the database is large;
* you want records written incrementally;
* you want lower memory usage during export.

Example:

```bash
firestore-backup export \
  -c key.json \
  --format ndjson \
  -o out/dump.ndjson
```

Phase 2 automatically detects both formats.

For large production databases, **NDJSON is generally the safer choice**.

---

# Firestore export

By default, the exporter:

1. discovers root collections;
2. reads their documents;
3. recursively discovers subcollections below reached documents;
4. serialises supported Firestore values;
5. writes a complete JSON or NDJSON dump.

Example:

```bash
firestore-backup export \
  -c /secure/path/key.json \
  -o out/dump.json
```

---

## Useful export options

| Option                               | Purpose                                          |
| ------------------------------------ | ------------------------------------------------ |
| `--format ndjson`                    | Stream records instead of building one JSON tree |
| `--collections users orders`         | Export only selected root collections            |
| `--collection-groups orders lines`   | Query known collection IDs globally              |
| `--limit 100`                        | Limit documents per collection/query             |
| `--no-subcollections`                | Ignore subcollections                            |
| `--subcollection-names orders lines` | Traverse only selected child collection IDs      |
| `--max-depth 20`                     | Explicitly limit recursive depth                 |
| `--page-size 300`                    | Configure query page size                        |
| `--snapshot`                         | Use one fixed Firestore `read_time`              |
| `--read-time ...`                    | Use an explicit RFC3339 `read_time`              |
| `--compact`                          | Generate compact JSON                            |

The default recursive depth is **100**, matching Firestore's documented maximum subcollection depth.

Official Firestore limits:

https://firebase.google.com/docs/firestore/quotas

---

# Orphan subcollections

Firestore can contain a subcollection even when its parent document no longer exists.

For example:

```text
users/alice                 ← missing
users/alice/orders/123      ← still exists
```

A normal recursive walk cannot discover that subcollection because there is no parent document to traverse.

If you know the collection IDs that may exist in this situation, use:

```bash
firestore-backup export \
  -c /secure/path/key.json \
  --collection-groups orders lines auditEvents \
  -o out/dump.json
```

Documents found both through recursion and collection-group queries are de-duplicated.

---

# Consistent reads

Without additional options, an export is a live traversal.

If documents change while the export is running, different records may reflect different moments in time.

For a fixed application-level Firestore read timestamp:

```bash
firestore-backup export \
  -c key.json \
  --snapshot \
  -o out/snapshot.json
```

Or provide an explicit Firestore `read_time`:

```bash
firestore-backup export \
  -c key.json \
  --read-time 2026-08-12T20:00:00Z \
  -o out/snapshot.json
```

`--snapshot` and `--read-time` cannot be used together.

Firestore restricts how old a normal `read_time` may be. Older historical reads may require Point-in-Time Recovery.

For Firestore-native disaster recovery, use Google's managed Firestore backup and restore features instead of treating this tool as a replacement.

---

# Atomic output

Exports are not written directly over an existing successful dump.

Instead:

```text
temporary file
      │
      ▼
complete export
      │
      ▼
atomic replacement
      │
      ▼
final dump
```

If an export fails:

* the incomplete temporary file is removed;
* the existing successful dump is preserved;
* the command exits with an error.

The CLI also refuses to use the credential file itself as the export target.

This is intentional: a backup tool should fail loudly rather than destroy a valid backup.

---

# Truncated dump detection

Versioned dumps include an explicit completion marker.

This is particularly important for NDJSON.

A file may contain many individually valid JSON lines while still being incomplete because the process stopped halfway through.

If the expected completion marker is missing, phase 2 rejects the dump rather than treating it as complete.

---

# Firestore types

Some Firestore values do not have a native JSON equivalent.

They are stored using explicit envelopes.

Examples:

```json
{
  "__fs_type__": "timestamp",
  "value": "2026-08-12T20:00:00+00:00"
}
```

```json
{
  "__fs_type__": "geopoint",
  "latitude": 41.9028,
  "longitude": 12.4964
}
```

```json
{
  "__fs_type__": "reference",
  "path": "users/abc123"
}
```

```json
{
  "__fs_type__": "bytes",
  "value": "AAH/IGJpbmFyeQ=="
}
```

```json
{
  "__fs_type__": "vector",
  "value": [0.1, 0.2, 0.3]
}
```

Supported values include:

* strings;
* integers;
* doubles;
* booleans;
* null;
* arrays;
* maps;
* timestamps;
* GeoPoints;
* document references;
* bytes;
* Firestore vectors;
* `NaN`;
* `Infinity`;
* `-Infinity`.

A real Firestore map containing its own `__fs_type__` field is escaped safely so it cannot be confused with internal metadata.

Unknown Firestore value types cause the export to fail explicitly instead of being silently converted to strings.

---

# JSON / NDJSON → SQL

Generate SQL with:

```bash
firestore-backup sql \
  out/dump.json \
  -o out/migration.sql \
  --dialect postgres
```

Supported targets:

```text
postgres
postgresql
mysql
mariadb
sqlite
```

There is also a `generic` renderer.

It should be treated as an ANSI-oriented starting point for manual adaptation, **not** as SQL guaranteed to run unchanged on every relational database.

---

## SQL options

| Option                   | Purpose                                           |
| ------------------------ | ------------------------------------------------- |
| `--maps flatten\|json`   | Flatten maps into columns or keep them as JSON    |
| `--arrays json\|table`   | Keep arrays as JSON or normalise into side tables |
| `--separator _`          | Separator for flattened identifiers               |
| `--drop`                 | Add `DROP TABLE IF EXISTS`                        |
| `--skip-existing`        | Ignore rows with an already-existing generated PK |
| `--all-nullable`         | Do not infer mandatory user fields                |
| `--table-prefix fs_`     | Prefix generated table names                      |
| `--batch-size 200`       | Number of rows per `INSERT`                       |
| `--preserve-case`        | Preserve identifier case                          |
| `--no-foreign-keys`      | Disable generated foreign keys                    |
| `--no-transaction`       | Disable transaction wrapper                       |
| `--data-output data.sql` | Split schema and data into separate files         |

Generated SQL files also use atomic replacement.

The CLI refuses to overwrite the source dump using either:

```text
--output
```

or:

```text
--data-output
```

---

# How Firestore documents become SQL rows

Firestore document IDs are only unique within their own collection path.

For example:

```text
users/alice/orders/001
users/bob/orders/001
```

Both documents have:

```text
_id = 001
```

Using `_id` directly as a SQL primary key would therefore cause a collision.

`firestore-backup` uses a technical primary key derived from the **complete Firestore document path**.

| Firestore concept             | SQL representation |
| ----------------------------- | ------------------ |
| Complete document identity    | `_pk`              |
| Original document ID          | `_id`              |
| Complete document path        | `_path`            |
| Parent identity               | `_parent_pk`       |
| Original parent document ID   | `_parent_id`       |
| Complete parent document path | `_parent_path`     |

`_pk` is the SHA-256 digest of the complete Firestore path.

That means these remain distinct:

```text
users/alice/orders/001
users/bob/orders/001
```

even though both have the same document ID.

---

# Collision-safe field names

Firestore field names may collide after conversion to SQL identifiers.

For example:

```json
{
  "_id": "real user data",
  "a": {
    "b": 1
  },
  "a_b": 2
}
```

A naïve flattening strategy could silently overwrite data.

`firestore-backup` keeps the internal schema structural and resolves SQL identifier collisions deterministically.

For example:

```text
_id
_id_2

a_b
a_b_2
```

The same strategy applies to table names.

A root collection named:

```text
a__b
```

and a nested collection path:

```text
a/{document}/b
```

remain separate even if their first human-readable SQL name would otherwise collide.

---

# SQL type mapping

Typical mappings include:

| Firestore | SQL                            |
| --------- | ------------------------------ |
| string    | text                           |
| integer   | bigint                         |
| double    | floating point                 |
| boolean   | boolean                        |
| timestamp | timestamp / datetime           |
| bytes     | binary / base64 representation |
| geopoint  | latitude + longitude columns   |
| map       | flattened columns or JSON      |
| array     | JSON or side table             |
| vector    | JSON envelope                  |

Actual SQL types vary by dialect.

For example:

* PostgreSQL uses `BYTEA`;
* MySQL uses `LONGBLOB`;
* timestamp syntax differs between engines.

---

# Schema inference

Firestore does not enforce a relational schema.

The SQL schema must therefore be inferred from the exported records.

Rules include:

* a field missing from some documents becomes nullable;
* integer + floating-point values promote to floating point;
* incompatible scalar types fall back to text;
* a field observed only as `null` is retained as nullable text.

A field that never appears in the exported sample cannot be inferred.

If you generated the dump using:

```text
--limit
```

consider generating SQL with:

```text
--all-nullable
```

until you have inspected the complete dataset.

---

# JSON is the source of truth

The Firestore dump is designed to preserve Firestore data more faithfully than the relational SQL representation.

SQL generation necessarily changes some semantics.

For example, SQL `NULL` generally cannot distinguish between:

```text
field missing
```

and:

```text
field explicitly set to null
```

Similarly, timestamp precision may vary between database engines.

For that reason:

> **Keep the JSON or NDJSON dump. Do not treat the generated SQL as the only archival copy.**

The SQL output is primarily a migration representation.

---

# Foreign keys and missing parents

Firestore may contain child documents whose parent document no longer exists.

A relational foreign key to that missing parent would fail.

If you need to preserve such orphan rows exactly:

```bash
firestore-backup sql \
  out/dump.json \
  -o out/migration.sql \
  --dialect postgres \
  --no-foreign-keys
```

SQLite output does not add post-hoc foreign keys in the same way as PostgreSQL and MySQL.

---

# Strings containing NUL

Firestore strings may contain:

```text
\u0000
```

Not every SQL text implementation can represent an embedded NUL byte.

For example, PostgreSQL text cannot.

Instead of silently stripping or modifying the value, phase 2 fails explicitly.

For binary data, prefer Firestore bytes instead of storing raw binary content inside strings.

---

# Read cost

Firestore pricing depends on:

* database location;
* billing model;
* number of document reads;
* queries performed;
* other Google Cloud pricing rules.

A full export normally reads at least every exported document once.

Additional traversal strategies such as collection-group queries may cause additional reads.

Official pricing:

https://cloud.google.com/firestore/pricing

For a first production test:

```bash
firestore-backup export \
  -c /secure/path/key.json \
  --limit 50 \
  -o out/probe.json
```

Inspect the dump and Firestore usage before running a very large export.

---

# Important limitations

`firestore-backup` is designed for portable export, inspection and SQL migration.

It is **not** a replacement for every Firestore-native backup mechanism.

Keep these limitations in mind:

* Firestore-native disaster recovery should use Google's managed backup/restore tools.
* A normal live export can observe documents from different moments in time.
* Orphan subcollections can only be recovered when their collection-group IDs are known.
* SQL is a relational representation, not a byte-for-byte inverse of Firestore.
* SQL schema inference depends on the documents present in the dump.
* Generated SQL should always be validated before importing into production.
* Database-engine versions outside those tested by CI may behave differently.

---

# Development

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the test suite:

```bash
python -m pytest -q
```

The tests cover, among other things:

* recursive Firestore export;
* keyset pagination;
* duplicate child IDs under different parents;
* collection-group orphan recovery;
* atomic file replacement;
* truncated dump detection;
* Firestore type preservation;
* `__fs_type__` collisions;
* reserved metadata field collisions;
* flattened-map collisions;
* table-name collisions;
* SQL escaping;
* reserved SQL identifiers;
* side-table arrays;
* parent/child relationships;
* SQLite execution and round-trip verification;
* PostgreSQL rendering;
* MySQL rendering;
* SQLite rendering.

CI also executes generated SQL against PostgreSQL 16 and MySQL 8.4 in addition to the SQLite test suite.

---

# Project structure

```text
firestore_backup/
  values.py
  client.py
  export.py
  loader.py
  infer.py
  sql.py
  generate.py
  cli.py

tests/
  mock_firestore.py
  conftest.py
  test_export.py
  test_sql.py
  test_edge_cases.py
  test_cli.py
  run_all.py
```

### Main modules

| File          | Responsibility                              |
| ------------- | ------------------------------------------- |
| `values.py`   | Firestore values ↔ JSON envelopes           |
| `client.py`   | Authentication and Firestore client         |
| `export.py`   | Traversal, pagination and collection groups |
| `loader.py`   | JSON / NDJSON loading                       |
| `infer.py`    | Structural flattening and schema inference  |
| `sql.py`      | SQL dialects, DDL and literals              |
| `generate.py` | SQL-generation orchestration                |
| `cli.py`      | Command-line interface and atomic writes    |

---

# Contributing

Bug reports and pull requests are welcome.

Before contributing, read:

* [CONTRIBUTING.md](CONTRIBUTING.md)
* [SECURITY.md](SECURITY.md)

Please never attach:

* real service-account credentials;
* private keys;
* production Firestore exports containing sensitive information.

Use minimal synthetic fixtures whenever possible.

---

# Security

If you discover a security issue, follow the process described in:

[SECURITY.md](SECURITY.md)

Do not publish credentials, private dumps or exploitable security details in a public issue.

---

# License

Apache License 2.0.

See:

* [LICENSE](LICENSE)
* [NOTICE](NOTICE)