# Backup and restore

TLG-IPAM keeps everything in one SQLite file, `/data/ipam.db`, in the container's `ipam-data` volume. `GET /api/v1/backup` returns a consistent snapshot of it, made with SQLite's online backup API while the app keeps running. A copy of the live file taken any other way can miss writes that are still in the write-ahead log.

This page sets up a scheduled pull of that snapshot from a different machine, and describes how to restore from one. The examples use `ipam.home.arpa` for the server, and `backup-host` and `ipam-host` for the machine that stores the backups and the machine that runs the container. Replace them with yours.

## Scheduled pull

Run the pull on a host other than the one that runs the container. A backup stored beside the thing it protects is lost with it.

The host needs `curl` and the `sqlite3` command-line tool (`apt install sqlite3`).

### The API key

Store the key in a file that only the backup user can read. The file holds one line, a complete HTTP header, so the key never appears in a process listing:

```sh
sudo install -d -m 700 -o backup -g backup /var/backups/tlg-ipam
sudo install -m 600 -o backup -g backup /dev/null /etc/tlg-ipam-backup.header
echo 'X-API-Key: <your API_KEY>' | sudo tee /etc/tlg-ipam-backup.header >/dev/null
```

### The pull script

Save as `/usr/local/bin/tlg-ipam-backup`, mode `755`:

```sh
#!/bin/sh
# Pull a snapshot of TLG-IPAM into a dated file, check it, then prune old ones.
set -eu

URL="${TLG_IPAM_URL:-https://ipam.home.arpa}"
HEADER_FILE="${TLG_IPAM_HEADER_FILE:-/etc/tlg-ipam-backup.header}"
DIR="${TLG_IPAM_BACKUP_DIR:-/var/backups/tlg-ipam}"
KEEP="${TLG_IPAM_KEEP:-30}"          # how many snapshots to keep

umask 077
mkdir -p "$DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
partial="$DIR/.ipam-$stamp.db.partial"
trap 'rm -f "$partial"' EXIT

fail() { echo "tlg-ipam-backup: $*" >&2; exit 1; }

# Only a plain 200 counts. curl does not follow redirects here, so a login redirect or a
# proxy error page cannot pass for a snapshot.
code=$(curl --silent --show-error --max-time 120 --header @"$HEADER_FILE" \
  --output "$partial" --write-out '%{http_code}' "$URL/api/v1/backup")
[ "$code" = "200" ] || fail "HTTP $code from $URL/api/v1/backup"

# A truncated, empty or foreign file must never become a "backup". immutable=1 opens the
# file read-only without creating -wal/-shm files beside it (the snapshot is in WAL mode).
[ -s "$partial" ] || fail "the response was empty"
result=$(sqlite3 "file:$partial?immutable=1" 'PRAGMA integrity_check')
[ "$result" = "ok" ] || fail "integrity_check said: $result"
tables=$(sqlite3 "file:$partial?immutable=1" \
  "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('sections','subnets','hosts')")
[ "$tables" = "3" ] || fail "the file is a database, but not a TLG-IPAM one"

mv "$partial" "$DIR/ipam-$stamp.db"
echo "wrote $DIR/ipam-$stamp.db"

# Retention: keep the newest $KEEP snapshots. This runs only after a successful pull,
# so a run of failed pulls never eats the backups that are already there.
ls -1 "$DIR"/ipam-*.db | sort -r | tail -n +"$((KEEP + 1))" |
  while read -r old; do rm -- "$old"; done
```

The names sort by date, so the newest snapshot is the last in `ls`. With the default of 30 and one pull a day, that is thirty days of history. Raise `TLG_IPAM_KEEP` for a longer one, or if you pull more often than daily.

Run it once by hand before scheduling it:

```sh
sudo -u backup /usr/local/bin/tlg-ipam-backup
```

### Schedule it: systemd timer

`/etc/systemd/system/tlg-ipam-backup.service`:

```ini
[Unit]
Description=Pull a TLG-IPAM snapshot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=backup
ExecStart=/usr/local/bin/tlg-ipam-backup
```

`/etc/systemd/system/tlg-ipam-backup.timer`:

```ini
[Unit]
Description=Daily TLG-IPAM snapshot

[Timer]
OnCalendar=*-*-* 02:30:00
RandomizedDelaySec=10min
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now tlg-ipam-backup.timer
systemctl list-timers tlg-ipam-backup.timer     # next run
journalctl -u tlg-ipam-backup.service           # what the last run said
```

`Persistent=true` runs a pull that was missed because the host was off.

### Schedule it: cron

If you prefer cron, put this in the `backup` user's crontab (`sudo -u backup crontab -e`):

```cron
MAILTO=you@example.com
30 2 * * * /usr/local/bin/tlg-ipam-backup >/dev/null
```

Cron mails whatever a job prints, not its exit status. The script prints its success line on stdout and every failure on stderr, so discarding stdout leaves a mail on failure and silence on success. Do not redirect stderr to a log file as well, or nothing will be mailed. This needs a working local mail setup on the host; if you have none, use the systemd timer instead.

### Know when it stops working

A backup job that fails silently is the usual way to find out you have no backups. Either alert on a failed run (systemd: `OnFailure=` on the service; cron: the `MAILTO=` line above), or look now and then at the newest file's date: `ls -1 /var/backups/tlg-ipam/ipam-*.db | sort | tail -1`.

### A second copy

The `ipam-data` volume is also worth including in the hypervisor's own backup job, so that the database has a second copy that does not depend on this pull. A hypervisor snapshot of a running database is crash-consistent, and SQLite recovers from that on its next start. The pulled snapshot is the one to restore from first.

## Restore

A restore replaces the database with a snapshot. Anything written after the snapshot was taken is gone. The Changes log is part of the database, so it also goes back to the snapshot. The restore itself is not logged.

The one rule behind the steps: **stop the app, remove the old database's `-wal` and `-shm` files with it, then copy the snapshot in.** A leftover `ipam.db-wal` from the old database, applied to the restored file, corrupts it.

Run this on the machine that runs the container (`ipam-host`), from the directory that holds `docker-compose.yml` and `.env`. If the pull host is a different machine, copy the snapshot over first (`scp backup-host:/var/backups/tlg-ipam/ipam-20261113T023000Z.db .`). It needs `sqlite3` there too.

It is a script, not a block to paste, so that `set -eu` stops it at the first failed check: a bad snapshot is refused before the app is stopped, and nothing is moved until the snapshot has passed. Save it as `tlg-ipam-restore`:

```sh
#!/bin/sh
# Usage: ./tlg-ipam-restore restore <snapshot.db>
#        ./tlg-ipam-restore undo <before-restore-directory-name>
set -eu

mode="${1:-}"
arg="${2:-}"
[ -n "$arg" ] || { sed -n '2,3p' "$0" >&2; exit 2; }
fail() { echo "tlg-ipam-restore: $*" >&2; exit 1; }

check_up() {
  docker compose up -d ipam
  # The retry covers the second or two the app takes to start.
  curl -fsS --retry 10 --retry-delay 1 --retry-all-errors http://localhost:8080/health
  echo
}

case "$mode" in
restore)
  [ -s "$arg" ] || fail "not found, or empty: $arg"
  snap="$(cd "$(dirname "$arg")" && pwd)/$(basename "$arg")"

  # 1. The snapshot must pass before anything is touched.
  result=$(sqlite3 "file:$snap?immutable=1" 'PRAGMA integrity_check')
  [ "$result" = "ok" ] || fail "integrity_check said: $result"

  # 2. Stop the app, so nothing writes while the file is replaced.
  docker compose stop ipam

  # 3. Move the current database (and its -wal and -shm) aside, then copy the snapshot in.
  docker compose run --rm --no-deps -v "$snap:/restore/ipam.db:ro" --entrypoint sh ipam -c '
    set -eu
    cd /data
    aside="before-restore-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir "$aside"
    for f in ipam.db ipam.db-wal ipam.db-shm; do
      if [ -e "$f" ]; then mv "$f" "$aside/"; fi
    done
    cp /restore/ipam.db /data/ipam.db
    echo "the replaced database is in /data/$aside"
  '

  # 4. Start the app and check it answers.
  check_up
  ;;

undo)
  case "$arg" in
    */* | *..*) fail "give the directory name only, e.g. before-restore-20261113T044806Z" ;;
    before-restore-[0-9]*) ;;
    *) fail "not a before-restore directory name: $arg" ;;
  esac
  # Look for the saved database before stopping anything.
  docker compose run --rm --no-deps --entrypoint test ipam -f "/data/$arg/ipam.db" ||
    fail "no $arg/ipam.db in the volume; nothing changed"
  docker compose stop ipam
  docker compose run --rm --no-deps --entrypoint sh ipam -c '
    set -eu
    cd /data
    aside="$1"
    # Check the saved database is there before the current one is removed.
    [ -f "$aside/ipam.db" ] || { echo "no $aside/ipam.db in the volume; nothing changed" >&2; exit 1; }
    rm -f ipam.db ipam.db-wal ipam.db-shm
    mv "$aside"/ipam.db* .
    rmdir "$aside"
  ' sh "$arg"
  check_up
  ;;

*)
  fail "first argument must be restore or undo"
  ;;
esac
```

```sh
chmod +x tlg-ipam-restore
./tlg-ipam-restore restore ipam-20261113T023000Z.db
```

If the script fails before it stops the app, nothing has changed. If it fails after that, the app is down: read the error and fix the cause. The old database is either still in place or in the `before-restore-` directory, and running the script again is safe.

Then look at the data: open the web UI, or compare a count from the API with what you expect, for example the number of subnets:

```sh
curl -fsS -H "X-API-Key: $API_KEY" http://localhost:8080/api/v1/subnets | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"]), "subnets")'
```

The database that was replaced is in `/data/before-restore-<timestamp>/` inside the volume (all of `ipam.db`, `ipam.db-wal` and `ipam.db-shm` that existed); the script printed the name. To undo the restore, run `./tlg-ipam-restore undo before-restore-<timestamp>`. When you are sure about the restore, delete that directory with `docker compose run --rm --no-deps --entrypoint rm ipam -r /data/before-restore-<timestamp>`.

Notes:

- Restore into the same version of the image that made the snapshot, or a newer one. A newer image adds any missing columns on startup. An older image may not understand a snapshot from a newer one.
- MCP clients and open browser tabs reconnect on their own after the restart; a client that does not needs restarting.
- A snapshot is a plain SQLite file. To read one without restoring it, open it with `sqlite3 ipam-20261113T023000Z.db`.

### Practice it once

A restore you have never run is a guess. After the first scheduled pull works, restore that snapshot into a throwaway instance (a second checkout with its own `docker compose -p restore-test`, or `uvicorn` on another port with `DB_PATH` pointing at a copy), and check the data. Do it before you need it.
