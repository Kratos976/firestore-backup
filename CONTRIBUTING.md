# Contributing

Thanks for taking the time. Bug reports, SQL-dialect fixes and real-world
Firestore edge cases are welcome.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
pytest -q
```

`tests/run_all.py` is kept for compatibility and invokes the same pytest suite.
No real Firestore project is needed for the unit/end-to-end tests: phase 1 uses
`tests/mock_firestore.py`. SQLite SQL is executed for real against an in-memory
database.

CI additionally exercises the supported Python versions. Database-specific
rendering tests must be added whenever dialect behaviour changes.

## Reporting a bug

The most useful report contains a **minimal synthetic document shape**, not a
real production record. For example:

```json
{"field": {"nested": [1, "two"]}}
```

Include the command you ran, the dialect, expected behaviour and actual
behaviour. For phase 2, the output of `firestore-backup inspect` on a synthetic
or redacted dump is often enough.

> Never attach service-account keys, access tokens, real exports or confidential
> production values to a public issue.

Security-sensitive reports should follow [SECURITY.md](SECURITY.md).

## Pull requests

- Add a regression test for every bug fix.
- Put hostile mapping/escaping cases in `tests/test_edge_cases.py` or
  `tests/test_sql.py` as appropriate.
- Keep phase 2 runtime dependency-free. `values.py`, `loader.py`, `infer.py`,
  `sql.py` and `generate.py` must continue to work with the standard library.
- Preserve structured internal identities. Do not use a human SQL name as the
  identity of a Firestore field or collection path.
- Never make `_id` the relational primary key for a collection-group table:
  Firestore IDs are only unique inside one collection path.
- Fail loudly rather than silently dropping, merging or overwriting data.
- Keep comments focused on *why* a choice exists, not on restating the code.

## Adding or changing a SQL dialect

`Dialect` lives in `firestore_backup/sql.py`; dialect aliases are registered in
`DIALECTS`.

When changing a dialect:

1. verify identifier limits and quoting;
2. verify DDL feature flags independently (`IF NOT EXISTS`, post-hoc foreign
   keys, conflict-ignore syntax, etc.);
3. add rendering assertions to `tests/test_sql.py`;
4. where practical, execute the produced SQL against the real database engine
   in CI rather than relying only on string assertions.

The `generic` dialect is intentionally conservative and is not an execution
compatibility promise across all engines.

## Before opening a PR

```bash
python -m compileall -q firestore_backup tests
pytest -q
python -m build
```

Also check the diff for credentials and generated data before pushing.
