#!/bin/sh
set -e

echo "Waiting for PostgreSQL..."
python - <<'PY'
import os
import time

import psycopg

dsn = (
    f"dbname={os.getenv('POSTGRES_DB', 'accessforge')} "
    f"user={os.getenv('POSTGRES_USER', 'accessforge')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'accessforge')} "
    f"host={os.getenv('POSTGRES_HOST', 'db')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')}"
)

for _ in range(30):
    try:
        with psycopg.connect(dsn):
            print("PostgreSQL is ready.")
            break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("Database did not become ready in time.")
PY

python manage.py migrate --noinput
python manage.py collectstatic --noinput
python scripts/bootstrap.py

exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
