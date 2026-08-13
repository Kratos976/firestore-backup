# Releasing firestore-backup

This project treats a release tag as a statement about data integrity. Do not tag
`v1.0.0` until the real-project smoke test and repository CI are green.

## 1. Local release gate

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
python -m compileall -q firestore_backup tests
pytest -q
python -m build
```

Inspect the tree before committing:

```bash
git status --short
git diff --check
git diff
```

Never stage a service-account key, dump, generated database or SQL file.

## 2. Real Firestore smoke test

Use a read-only identity and an output directory ignored by Git:

```bash
mkdir -p out
firestore-backup export \
  --credentials /secure/path/service-account.json \
  --limit 10 \
  --no-subcollections \
  -o out/probe.json

firestore-backup inspect out/probe.json --dialect sqlite
firestore-backup sql out/probe.json -o out/probe.sql --dialect sqlite --drop
```

Execute the SQLite script rather than only reading it:

```bash
python - <<'PY'
import sqlite3
conn = sqlite3.connect('out/probe.sqlite')
conn.executescript(open('out/probe.sql', encoding='utf-8').read())
print(conn.execute("select name from sqlite_master where type='table' order by name").fetchall())
conn.close()
PY
```

Then test a complete export. If your Firestore model can contain orphan
subcollections, include their known collection IDs with `--collection-groups`.

## 3. Push and let CI prove the package

Push through your normal branch/pull-request workflow. The `CI` workflow checks:

- Python 3.9 through 3.14;
- macOS and Windows smoke runs;
- real execution of generated SQL on PostgreSQL 16 and MySQL 8.4;
- package build.

Do not tag while CI is red.

## 4. Tag

Ensure `pyproject.toml` and `firestore_backup.__version__` both report `1.0.0`.
Then:

```bash
git tag -a v1.0.0 -m "firestore-backup 1.0.0"
git push origin v1.0.0
```

`.github/workflows/release.yml` verifies that the tag matches the package version,
runs the tests/build again and creates the GitHub Release with wheel and source
distribution attached.

## 5. PyPI

Configure PyPI Trusted Publishing for this GitHub repository and the `pypi`
GitHub environment. No long-lived PyPI API token should be stored in repository
secrets.

After the GitHub Release exists, run the `Publish to PyPI` workflow manually and
provide the existing tag, for example `v1.0.0`. The workflow checks out that exact
tag, retests it, verifies that the matching GitHub Release already exists, rebuilds
the package, verifies the version and publishes through OIDC.

For a first publication, TestPyPI is a sensible rehearsal before the production
PyPI project.
