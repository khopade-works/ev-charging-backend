#!/usr/bin/env bash
# User journey against a running deployment: register -> start (with promo) -> end with a bill -> history.
# Safe on a non-empty database: it uses a random location, a 1 km radius and a unique promo code.
# Usage: BASE=http://127.0.0.1:8080 scripts/smoke.sh        Exits non-zero on the first failed step.
set -euo pipefail

BASE=${BASE:-http://127.0.0.1:8080}
BODY=$(mktemp)
trap 'rm -f "$BODY"' EXIT

step() {  # step "name" METHOD PATH EXPECTED_STATUS [JSON]
    local status
    status=$(curl -sS -o "$BODY" -w '%{http_code}' -X "$2" "$BASE$3" -H 'Content-Type: application/json' ${5:+-d "$5"})
    if [[ "$status" != "$4" ]]; then
        echo "FAIL  $1: $2 $3 -> HTTP $status, expected $4: $(cat "$BODY")" >&2
        exit 1
    fi
    echo "ok    $1 ($2 $3 -> $status)"
}
field() { jq -er "$1" "$BODY"; }

LAT=$(( RANDOM % 120 - 60 )).$RANDOM
LON=$(( RANDOM % 340 - 170 )).$RANDOM
PROMO="SMOKE$RANDOM$RANDOM"

step "version" GET /version 200
echo "      running $(field .git_sha)"
step "register driver" POST /drivers 201 '{"name":"Smoke","vehicle":{"model":"Nexon EV","connector_types":["AC","DC"]}}'
DRIVER=$(field .id)
step "register station" POST /stations 201 "{\"name\":\"Smoke\",\"lat\":$LAT,\"lon\":$LON,\"connectors\":[{\"type\":\"AC\",\"power_kw\":7.4},{\"type\":\"DC\",\"power_kw\":50}]}"
STATION=$(field .id)
step "add promo" POST /promos 201 "{\"code\":\"$PROMO\",\"percent_off\":10}"
step "start session" POST /sessions 201 "{\"driver_id\":$DRIVER,\"lat\":$LAT,\"lon\":$LON,\"radius_km\":1,\"connector_type\":\"AC\",\"promo_code\":\"$PROMO\"}"
SESSION=$(field .id)
[[ $(field .station_id) == "$STATION" ]] || { echo "FAIL  session landed on station $(field .station_id), expected $STATION" >&2; exit 1; }
step "second start is rejected" POST /sessions 409 "{\"driver_id\":$DRIVER,\"lat\":$LAT,\"lon\":$LON,\"radius_km\":1,\"connector_type\":\"AC\"}"
step "end session" POST "/sessions/$SESSION/end" 200 '{"energy_kwh":"12.5"}'
[[ $(field .status) == COMPLETED ]] || { echo "FAIL  session not COMPLETED" >&2; exit 1; }
echo "      billed $(field .bill.total) (discount $(field .bill.discount), multiplier $(field .bill.multiplier))"
step "driver history" GET "/drivers/$DRIVER/sessions" 200
[[ $(field '.completed | length') == 1 ]] || { echo "FAIL  history does not show the completed session" >&2; exit 1; }
step "delete promo" DELETE "/promos/$PROMO" 204
step "unknown session is 404" POST /sessions/999999999/end 404 '{"energy_kwh":"1"}'
echo "PASS  user journey against $BASE"
