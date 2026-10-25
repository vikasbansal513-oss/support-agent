# Spec pack (TDD V0 v1.1)

Copy into the repo as spec/. Source of truth when the TDD and this pack differ.

Run (Python 3.13, pydantic 2, pytest, email-validator):
  for d in contracts agent channels frontend; do (cd spec/$d && python -m pytest -q .); done
  python spec/evals/run.py
Database (Postgres 16; owner role runs schema, a login role that is a member of support_app_runtime runs tests):
  psql -d NEWDB -v ON_ERROR_STOP=1 -f spec/db/schema.sql
  PGUSER=<member_of_support_app_runtime> psql -d NEWDB -v ON_ERROR_STOP=1 -f spec/db/rls_tests.sql
