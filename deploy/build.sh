#!/usr/bin/env bash
# Build and push the txb-crm production image from a dev machine (no CI).
#
# Usage:
#   GH_TOKEN=<read-only PAT for Mygom-tech/txb-crm> ./deploy/build.sh [tag]
#
# The tag defaults to the current commit's short SHA. Every push also moves
# :latest, but Coolify should pin FRAPPE_VERSION to the SHA tag — rolling
# tags are how prod schema drift happened in the first place.
set -euo pipefail

REGISTRY_IMAGE="${REGISTRY_IMAGE:-ghcr.io/mygom-tech/txb-crm}"
FRAPPE_BRANCH="${FRAPPE_BRANCH:-version-15}"
FD_DIR="${FD_DIR:-/tmp/frappe_docker}"

# Resolve the repo from this script's own location — never from the caller's
# cwd (stray ancestor .git dirs have burned us before).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# commit sha + build timestamp: rebuilding the same commit (new base images,
# fixed tooling) must produce a NEW tag, or servers keep their cached copy
TAG="${1:-$(git -C "$REPO_ROOT" rev-parse --short HEAD)-$(date +%Y%m%d%H%M)}"

: "${GH_TOKEN:?Set GH_TOKEN to a read-only PAT that can clone Mygom-tech/txb-crm}"

if [ -d "$FD_DIR/.git" ]; then
  git -C "$FD_DIR" pull --ff-only
else
  git clone --depth 1 https://github.com/frappe/frappe_docker "$FD_DIR"
fi

# frappe_docker consumes apps.json as a BuildKit SECRET (id=apps_json), not a
# build arg — an unconsumed arg builds a frappe-only image with zero errors
# (we learned this in production). The secret is never written to image layers.
APPS_JSON_FILE="$(mktemp)"
# frappe/build:version-15 ships Git 2.39.5, whose default HTTP/2 transport fails
# public GitHub ref discovery inside `bench init` ("could not read Username for
# 'https://github.com'" / "expected flush after ref listing"). We keep the
# upstream frappe_docker checkout pristine and instead build from a temporary
# Containerfile derivative that forces the builder onto HTTP/1.1 (TXB-214).
#
# The derivative MUST live inside $FD_DIR (the build context), not global /tmp:
# BuildKit's `docker build -f <dockerfile>` sends the dockerfile's own parent as
# part of the context transfer, so a /tmp/tmp.* path makes the sender traverse
# global /tmp and abort on unrelated protected siblings like /tmp/.forticlient
# ("error from sender: open /tmp/.forticlient: permission denied") before any
# build stage runs (TXB-215). $FD_DIR already exists here (cloned/pulled above),
# and the hidden `.txb-hardened.*` name stays untracked in the frappe_docker
# checkout; the EXIT trap removes it on success and failure so it is never left
# behind or committed.
HARDENED_CONTAINERFILE="$(mktemp "$FD_DIR/.txb-hardened.Containerfile.XXXXXX")"
# One trap owns both temp files so the secret and generated Containerfile are
# removed on success and on any failure.
trap 'rm -f "$APPS_JSON_FILE" "$HARDENED_CONTAINERFILE"' EXIT
chmod 600 "$APPS_JSON_FILE"
# Substitute ONLY ${GH_TOKEN} so other $-signs in apps.json survive verbatim
GH_TOKEN="$GH_TOKEN" envsubst '$GH_TOKEN' \
  < "$REPO_ROOT/deploy/apps.json" > "$APPS_JSON_FILE"

# Inject the HTTP/1.1 git-config RUN before bench init. The transform asserts a
# single builder-stage anchor and hard-fails if the upstream layout shifts, so
# we never silently build an unhardened image.
python3 "$REPO_ROOT/deploy/harden_containerfile.py" \
  "$FD_DIR/images/layered/Containerfile" "$HARDENED_CONTAINERFILE"

# Context stays $FD_DIR (COPY paths + base images resolve against it); only the
# Dockerfile is swapped for the hardened derivative.
DOCKER_BUILDKIT=1 docker build "$FD_DIR" \
  -f "$HARDENED_CONTAINERFILE" \
  --secret "id=apps_json,src=$APPS_JSON_FILE" \
  --build-arg FRAPPE_PATH=https://github.com/frappe/frappe \
  --build-arg FRAPPE_BRANCH="$FRAPPE_BRANCH" \
  --build-arg CACHE_BUST="$(date +%s)" \
  -t "$REGISTRY_IMAGE:$TAG" \
  -t "$REGISTRY_IMAGE:latest"

# HARD GATE: refuse to push an image that doesn't contain the crm app.
echo "--- app-presence gate ---"
if ! docker run --rm --entrypoint ls "$REGISTRY_IMAGE:$TAG" apps | grep -qx crm; then
  echo "FATAL: 'crm' app missing from image — not pushing." >&2
  exit 1
fi
echo "ok: apps/crm present in image"

docker push "$REGISTRY_IMAGE:$TAG"
docker push "$REGISTRY_IMAGE:latest"

echo
echo "Pushed $REGISTRY_IMAGE:$TAG"
echo "Next: set FRAPPE_VERSION=$TAG in Coolify and redeploy."
