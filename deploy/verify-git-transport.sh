#!/usr/bin/env bash
# TXB-214 — local, no-push proof that the hardened builder resolves Frappe
# v15 over Git without the frappe/build:version-15 HTTP/2 discovery failure.
#
# Usage:
#   ./deploy/verify-git-transport.sh
#
# This builds ONLY the `builder` stage from the hardened Containerfile
# derivative, with an EMPTY apps_json secret so no GH_TOKEN and no network to
# the private fork are needed — the point under test is `bench init` reaching
# `https://github.com/frappe/frappe` branch version-15. It never tags a
# release image and never runs `docker push`. All temp files are removed on
# success and failure.
#
# PASS  = builder stage completes (git ls-remote / bench init discovery worked).
# FAIL  = the build errors out; if you see "could not read Username for
#         'https://github.com'" or "expected flush after ref listing", the
#         HTTP/1.1 hardening did not take effect (check the injection anchor).
set -euo pipefail

FRAPPE_BRANCH="${FRAPPE_BRANCH:-version-15}"
FD_DIR="${FD_DIR:-/tmp/frappe_docker}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [ -d "$FD_DIR/.git" ]; then
  git -C "$FD_DIR" pull --ff-only
else
  git clone --depth 1 https://github.com/frappe/frappe_docker "$FD_DIR"
fi

# Empty secret file: bench init then builds a frappe-only bench, which still
# exercises the exact `git ls-remote https://github.com/frappe/frappe
# version-15` path that fails without the transport fix.
APPS_JSON_FILE="$(mktemp)"
HARDENED_CONTAINERFILE="$(mktemp)"
trap 'rm -f "$APPS_JSON_FILE" "$HARDENED_CONTAINERFILE"' EXIT

python3 "$REPO_ROOT/deploy/harden_containerfile.py" \
  "$FD_DIR/images/layered/Containerfile" "$HARDENED_CONTAINERFILE"

echo "--- hardening injected before bench init ---"
grep -n "http.version HTTP/1.1" "$HARDENED_CONTAINERFILE"

echo "--- no-push builder-stage verification (target=builder) ---"
DOCKER_BUILDKIT=1 docker build "$FD_DIR" \
  -f "$HARDENED_CONTAINERFILE" \
  --target builder \
  --secret "id=apps_json,src=$APPS_JSON_FILE" \
  --build-arg FRAPPE_PATH=https://github.com/frappe/frappe \
  --build-arg FRAPPE_BRANCH="$FRAPPE_BRANCH" \
  --build-arg CACHE_BUST="$(date +%s)"

echo
echo "PASS: builder resolved frappe/frappe branch $FRAPPE_BRANCH over Git (no push performed)."
