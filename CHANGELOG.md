# Changelog

All notable changes to this project are documented here.

The project follows Semantic Versioning.

## [1.0.0] - 2026-08-12

### Added

- JSON and streaming NDJSON Firestore export.
- PostgreSQL, MySQL/MariaDB and SQLite SQL generation.
- Fixed-`read_time` exports through `--snapshot` and `--read-time`.
- `--collection-groups` to recover known orphan subcollections that cannot be
  discovered by walking existing parent documents alone.
- Atomic replacement for dump and SQL output files, with directory fsync where
  supported.
- Versioned dump metadata and a completion marker that detects truncated NDJSON.
- Guard rails preventing export output from overwriting credentials and SQL
  output from overwriting its source dump.
- Pytest suite with executable SQLite round-trip coverage.

### Data-integrity guarantees

- SQL document identity is now `_pk`, a SHA-256 digest of the complete Firestore
  document path; duplicate child document IDs under different parents no longer
  collide.
- Child relations use `_parent_pk`, with original `_parent_id` and
  `_parent_path` retained for inspection.
- User fields named like generated metadata (`_pk`, `_id`, `_path`, etc.) are
  preserved and safely renamed at the SQL-name layer.
- Flattened field collisions such as `a.b` versus a literal `a_b` are preserved
  as separate columns.
- Collection paths use structural internal identities, preventing a root
  collection such as `a__b` from colliding with `a/{doc}/b`.
- User maps containing `__fs_type__` are escaped and cannot be mistaken for
  Firestore type envelopes.
- Firestore Vector values retain an explicit vector envelope rather than being
  confused with ordinary arrays.
- Normalised scalar arrays include a parent-side count marker so empty arrays
  remain distinguishable from absent fields.
- Unknown Firestore SDK value types fail loudly instead of being silently
  stringified.
- Firestore `NaN`/`Infinity` values are retained as text in portable SQL rather
  than silently becoming `NULL`; embedded NUL text fails explicitly when SQL
  cannot preserve it safely.

### Changed

- PostgreSQL is the default SQL dialect.
- `generic` is documented as an ANSI-oriented manual-adaptation target rather
  than a universal execution dialect.
- MySQL rendering no longer emits unsupported `CREATE INDEX IF NOT EXISTS` and
  no longer uses broad `INSERT IGNORE` for `--skip-existing`.
- Generated SQL names and long constraint names are deterministic and
  collision-safe.
- MySQL user strings use `LONGTEXT`, avoiding Firestore-sized strings exceeding
  the much smaller `TEXT` capacity.
- Numeric CLI arguments reject invalid zero/negative values where appropriate.
- Recursive traversal now defaults to Firestore's 100-level subcollection limit instead of stopping at depth 8.
- Documentation now distinguishes normal recursive traversal from orphan
  collection-group recovery and documents the actual consistency model.
