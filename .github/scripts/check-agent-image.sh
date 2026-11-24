#!/usr/bin/env bash
# The scan agent image's start-up checks (spec §5.6), run by CI on every change and by
# the release workflow on each platform of the image it publishes (#24). Every container
# runs with no network at all, so no check sends a packet anywhere.
#
#   check-agent-image.sh IMAGE [VERSION [PLATFORM]]
#
# With VERSION, `--version` must print exactly it; with PLATFORM (linux/arm64, say), the
# containers run as that platform, under emulation where the host's is another.
set -euo pipefail

image=$1
version=${2:-}
platform=${3:-}

run() {
  docker run --rm --network none ${platform:+--platform "$platform"} "$@"
}

got=$(run "$image" --version)
echo "$image${platform:+ ($platform)} --version: $got"
if [ -n "$version" ] && [ "$got" != "$version" ]; then
  echo "::error::$image reports version $got, not $version"
  exit 1
fi
# with no configuration it refuses to start, naming the variable
out=$(run "$image" 2>&1) && { echo "::error::$image started with no configuration"; exit 1; }
grep -q "IPAM_URL is not set" <<<"$out"
# with a URL and neither token, nor one stored, it names what it needs (#22)
out=$(run -e IPAM_URL=http://127.0.0.1:8080 "$image" 2>&1) && { echo "::error::$image started with no token"; exit 1; }
grep -q "no agent token: set IPAM_ENROL_TOKEN" <<<"$out"
# the directory a volume mounts over for the stored token: root's, 0700
test "$(run --entrypoint stat "$image" -c '%U %a' /var/lib/tlg-ipam-agent)" = "root 700"
echo "$image${platform:+ ($platform)}: start-up checks passed"
