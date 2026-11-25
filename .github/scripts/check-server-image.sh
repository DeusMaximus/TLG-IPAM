#!/usr/bin/env bash
# The server image's checks (#173), run by CI on every change:
#   1. the image's user is uid and gid 10001, and so is the server process (pid 1);
#   2. on a new volume it starts and takes writes, and the database is that user's;
#   3. on a volume left as an image that ran as root left it (root's directory, root's
#      files, mode 0600, a before-restore directory), it refuses to start with the
#      one-time commands, and after them (README, "Upgrading from an image that ran as
#      root") /data is 10001's and 0700, and it starts, keeps the data and takes writes.
# Every container runs with no network: requests go to the server's loopback from inside
# its own container, so nothing leaves the host.
#
#   check-server-image.sh IMAGE
set -euo pipefail

image=$1
name="check-server-image-$$"
fresh="$name-fresh"
old="$name-old"
key=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')

cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm -f "$fresh" "$old" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() { echo "::error::$image: $*"; exit 1; }

# request METHOD PATH [BODY]: the status, then the body, of one request to the server,
# made by the server's own user from inside its container
request() {
  docker exec "$name" python -c '
import os, sys, urllib.error, urllib.request
method, path, body = sys.argv[1], sys.argv[2], (sys.argv[3].encode() if len(sys.argv) > 3 else None)
req = urllib.request.Request("http://127.0.0.1:8080" + path, data=body, method=method,
    headers={"X-API-Key": os.environ["API_KEY"], "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(r.status); print(r.read().decode(errors="replace")[:2000])
except urllib.error.HTTPError as e:
    print(e.code); print(e.read().decode(errors="replace")[:2000])
' "$@"
}

start() {  # start [docker run options…]: run the server on a volume and wait for /health
  docker run -d --name "$name" --network none -e API_KEY="$key" "$@" "$image" >/dev/null
  for _ in $(seq 60); do
    if [ "$(docker inspect -f '{{.State.Running}}' "$name")" != true ]; then
      docker logs "$name" >&2 || true
      fail "the server exited while starting"
    fi
    if request GET /health 2>/dev/null | head -1 | grep -qx 200; then return; fi
    sleep 1
  done
  docker logs "$name" >&2 || true
  fail "the server did not answer /health within 60 s"
}

stop() { docker stop "$name" >/dev/null && docker rm "$name" >/dev/null; }

expect() {  # expect STATUS METHOD PATH [BODY]
  local want=$1; shift
  local got
  got=$(request "$@")
  [ "$(head -1 <<<"$got")" = "$want" ] || fail "$* answered $(head -1 <<<"$got"), not $want: $(tail -n +2 <<<"$got")"
  tail -n +2 <<<"$got"
}

not_root() {  # the server process's real, effective, saved and filesystem ids
  local uid gid
  uid=$(docker exec "$name" awk '/^Uid:/ {print $2, $3, $4, $5}' /proc/1/status)
  gid=$(docker exec "$name" awk '/^Gid:/ {print $2, $3, $4, $5}' /proc/1/status)
  [ "$uid" = "10001 10001 10001 10001" ] || fail "the server process's uids are $uid"
  [ "$gid" = "10001 10001 10001 10001" ] || fail "the server process's gids are $gid"
  echo "server process: uid $uid, gid $gid"
}

# 1. The image's user
user=$(docker image inspect -f '{{.Config.User}}' "$image")
[ "$user" = "10001:10001" ] || fail "the image's USER is '$user', not 10001:10001"

# 2. A new volume
start -v "$fresh:/data"
not_root
expect 201 POST /api/v1/sections '{"name":"fresh volume"}' >/dev/null
expect 200 GET /api/v1/backup >/dev/null
owners=$(docker exec "$name" find /data ! -user 10001)
[ -z "$owners" ] || fail "files in a new volume not owned by 10001: $owners"
stop
echo "$image: a new volume starts and takes writes"

# 3. A volume as an image that ran as root left it. volume-nocopy keeps the volume's own
# directory as the runtime makes it, root's and 0755, as it was under an image with no /data.
mount="type=volume,source=$old,target=/data,volume-nocopy=true"
start --user 0:0 --mount "$mount"
expect 201 POST /api/v1/sections '{"name":"made as root"}' >/dev/null
stop
docker run --rm --network none --user 0:0 --mount "$mount" --entrypoint sh "$image" -c '
  set -eu
  chmod 600 /data/ipam.db*
  mkdir /data/before-restore-20260101T000000Z
  cp -p /data/ipam.db /data/before-restore-20260101T000000Z/
  ls -ln /data
'
if out=$(docker run --rm --network none -e API_KEY="$key" --mount "$mount" "$image" 2>&1); then
  fail "the server started on a volume it cannot write"
fi
grep -qF 'TLG-IPAM will not start: the database'"'"'s directory /data is not writable by this process (uid 10001, gid 10001)' <<<"$out" ||
  fail "the refusal on a root volume was not the expected message: $out"
grep -qF -- '--user root --entrypoint chown ipam -R 10001:10001 /data`' <<<"$out" ||
  fail "the refusal does not give the chown command: $out"
grep -qF -- '--user root --entrypoint chmod ipam 700 /data`' <<<"$out" ||
  fail "the refusal does not give the chmod command: $out"
echo "$image: refuses a root volume, naming the commands"
# the same volume named through a URI is refused too, before the storage check could be
# passed by: a file: name is not a path the check reads
if out=$(docker run --rm --network none -e API_KEY="$key" -e 'DB_PATH=file:/data/ipam.db?mode=rwc&uri=true' \
    --mount "$mount" "$image" 2>&1); then
  fail "the server started on a root volume named by a file: URI"
fi
grep -qxF 'TLG-IPAM will not start: DB_PATH must be a filesystem path or :memory:.' <<<"$out" ||
  fail "the refusal of a file: DB_PATH was not the expected message: $out"
echo "$image: refuses a file: DB_PATH"
# the README's one-time commands, as docker run in place of docker compose run
docker run --rm --network none --user root --mount "$mount" --entrypoint chown "$image" -R 10001:10001 /data
docker run --rm --network none --user root --mount "$mount" --entrypoint chmod "$image" 700 /data
start --mount "$mount"
mode=$(docker exec "$name" stat -c '%u:%g %a' /data)
[ "$mode" = "10001:10001 700" ] || fail "/data after the one-time commands is $mode, not 10001:10001 700"
not_root
expect 200 GET /api/v1/sections | grep -qF '"made as root"' || fail "the data made as root is gone"
expect 201 POST /api/v1/sections '{"name":"after the chown"}' >/dev/null
owners=$(docker exec "$name" find /data ! -user 10001)
[ -z "$owners" ] || fail "files not owned by 10001 after the chown: $owners"
stop
echo "$image: an upgraded root volume starts, keeps its data and takes writes"
echo "$image: server image checks passed"
