#!/usr/bin/env bash
# Promote one commit to the local cluster (the manual approval step of CD).
# Only a commit whose CI run passed, and only the image CI built and scanned for it, can be deployed.
# Usage: scripts/deploy.sh [<40-char git sha>]      default: the current HEAD
set -euo pipefail

REPO=khopade-works/ev-charging-backend
IMAGE=ghcr.io/khopade-works/ev-charging-backend
BASE=${BASE:-http://127.0.0.1:8080}
SHA=${1:-$(git rev-parse HEAD)}

echo "== gate: CI run for $SHA"
RUN_URL=$(gh run list -R "$REPO" --workflow CI --commit "$SHA" --status success --limit 1 --json url --jq '.[0].url // empty')
if [[ -z "$RUN_URL" ]]; then
    echo "no successful CI run for $SHA; refusing to deploy" >&2
    exit 1
fi
echo "   $RUN_URL"

echo "== artifact: $IMAGE:$SHA"
docker pull --quiet "$IMAGE:$SHA"
docker inspect --format '   digest {{index .RepoDigests 0}}' "$IMAGE:$SHA"
kind load docker-image --name ev "$IMAGE:$SHA"  # local cluster: no registry credentials inside the cluster

echo "== release: rolling update; a release that never turns Ready is rolled back automatically"
helm upgrade --install ev deploy/helm/ev-charging -n ev \
    --set image.tag="$SHA" \
    --rollback-on-failure --wait --timeout 5m \
    --description "sha=$SHA ci=$RUN_URL"
helm history ev -n ev --max 3

echo "== verify"
RUNNING=$(curl -fsS "$BASE/version" | jq -r .git_sha)
[[ "$RUNNING" == "$SHA" ]] || { echo "running $RUNNING, expected $SHA" >&2; exit 1; }
echo "   serving $RUNNING"
BASE=$BASE "$(dirname "$0")/smoke.sh"
