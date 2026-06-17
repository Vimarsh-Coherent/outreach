#!/bin/sh
set -e

echo "Waiting for database..."
until python -c "
import os, sys, time
import psycopg2
url = os.environ.get('SYNC_DATABASE_URL', '')
# postgresql+psycopg2://user:pass@host:port/db -> host, port, user, pass, db
if not url.startswith('postgresql'):
    sys.exit(1)
rest = url.split('://', 1)[1]
auth, loc = rest.rsplit('@', 1)
user, password = auth.split(':', 1)
host, dbpart = loc.split('/', 1)
host, port = (host.split(':', 1) + ['5432'])[:2]
dbname = dbpart.split('?', 1)[0]
for _ in range(60):
    try:
        psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=int(port))
        sys.exit(0)
    except psycopg2.OperationalError:
        time.sleep(1)
sys.exit(1)
"; do
  echo "  postgres not ready yet, retrying..."
  sleep 2
done

echo "Running migrations..."
alembic upgrade head

echo "Starting backend..."
exec "$@"
