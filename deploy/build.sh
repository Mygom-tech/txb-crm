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

REPO_ROOT="$(git rev-parse --show-toplevel)"
TAG="${1:-$(git -C "$REPO_ROOT" rev-parse --short HEAD)}"

: "${GH_TOKEN:?Set GH_TOKEN to a read-only PAT that can clone Mygom-tech/txb-crm}"

if [ -d "$FD_DIR/.git" ]; then
  git -C "$FD_DIR" pull --ff-only
else
  git clone --depth 1 https://github.com/frappe/frappe_docker "$FD_DIR"
fi

# Substitute ONLY ${GH_TOKEN} so other $-signs in apps.json survive verbatim
APPS_JSON_BASE64="$(GH_TOKEN="$GH_TOKEN" envsubst '$GH_TOKEN' \
  < "$REPO_ROOT/deploy/apps.json" | base64 -w0)"

docker build "$FD_DIR" \
  -f "$FD_DIR/images/layered/Containerfile" \
  --build-arg FRAPPE_PATH=https://github.com/frappe/frappe \
  --build-arg FRAPPE_BRANCH="$FRAPPE_BRANCH" \
  --build-arg APPS_JSON_BASE64="$APPS_JSON_BASE64" \
  -t "$REGISTRY_IMAGE:$TAG" \
  -t "$REGISTRY_IMAGE:latest"

# The clone URL (token included) can survive inside the image — check, and
# treat a hit as expected: keep the image private and the PAT read-only.
echo "--- token-leak check (app git remote inside image) ---"
docker run --rm "$REGISTRY_IMAGE:$TAG" \
  bash -c "cd apps/crm 2>/dev/null && git remote -v || true" | sed "s/${GH_TOKEN}/<REDACTED>/g"

docker push "$REGISTRY_IMAGE:$TAG"
docker push "$REGISTRY_IMAGE:latest"

echo
echo "Pushed $REGISTRY_IMAGE:$TAG"
echo "Next: set FRAPPE_VERSION=$TAG in Coolify and redeploy."
