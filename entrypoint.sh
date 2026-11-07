#!/bin/sh
set -e
# Safe to run on every start: both commands are idempotent.
python manage.py migrate --noinput
python manage.py createcachetable
exec gunicorn config.wsgi --bind "0.0.0.0:${PORT:-8080}" --workers "${WEB_CONCURRENCY:-2}" --threads 4 --access-logfile -
