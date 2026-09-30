#!/bin/sh
set -eu
/usr/bin/docker exec -i klock-klock-1 python - <<'PY'
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
os.umask(0o077)
folder=Path('/data/backups')
folder.mkdir(exist_ok=True)
now=datetime.now(timezone.utc)
target=folder / ('klock-'+now.strftime('%Y%m%dT%H%M%SZ')+'.db')
source=sqlite3.connect('file:/data/klock.db?mode=ro',uri=True)
backup=sqlite3.connect(target)
try:
    source.backup(backup)
    assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
finally:
    backup.close()
    source.close()
for file in folder.glob('klock-*.db'):
    if file.stat().st_mtime < (now-timedelta(days=14)).timestamp():
        file.unlink()
print('Klock database backup completed')
PY
