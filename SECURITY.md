# Security policy

## Supported versions

Security fixes are applied to the latest 1.x release line.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose
credentials, bypass data-integrity guarantees or leak database contents.

Use GitHub's private security-advisory flow for this repository when available:

<https://github.com/Kratos976/firestore-backup/security/advisories/new>

Include a minimal reproduction and avoid sending real production credentials or
database dumps unless a private follow-up explicitly requires them.

## Credential and data model

`firestore-backup` is local-first:

- service-account material is read locally and passed to Google's Firestore SDK;
- the project does not upload credentials or dump contents to a project-operated
  server;
- JSON/NDJSON and SQL outputs are written to paths chosen by the user;
- output files may contain the complete contents of a Firestore database and
  must therefore be protected like the source database itself.

Use a read-only Firestore identity where possible. Keep service-account keys
outside the repository and rotate any key that is accidentally committed or
shared.

## Integrity expectations

Silent data loss is treated as a security/integrity defect. Changes affecting
path identity, flattened names, generated metadata, value envelopes or SQL
conflict handling should include regression tests for collision cases.
