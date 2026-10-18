#!/usr/bin/env bash
# Promotes one commit to the local cluster: the manual approval step of CD.
# Refuses a commit without a green CI run, and deploys only the image CI built and scanned for it.
# Usage: scripts/deploy.sh <40-char git sha>
set -euo pipefail
cd "$(dirname "$0")/.."
SHA=$1

RUN=$(gh run list -R khopade-works/ev-charging-backend --workflow CI --commit "$SHA" --status success --json url --jq '.[0].url // empty')
[[ -n "$RUN" ]] || { echo "no green CI run for $SHA" >&2; exit 1; }
echo "CI: $RUN"
# The kubelet pulls CI's image from GHCR (public). A release that never turns Ready is rolled back automatically.
helm upgrade --install ev deploy/helm/ev-charging -n ev --set image.tag="$SHA" \
  --rollback-on-failure --wait --timeout 5m --description "sha=$SHA ci=$RUN"
[[ $(curl -fsS http://127.0.0.1:8080/version | jq -r .git_sha) == "$SHA" ]]
scripts/smoke.sh
