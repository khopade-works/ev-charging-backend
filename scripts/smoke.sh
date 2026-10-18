#!/usr/bin/env bash
# User journey against a running deployment: register -> start a session with a promo -> end it with a bill -> history.
# Safe on a non-empty database (random location, 1 km radius, unique promo). Exits non-zero on the first failed step.
set -euo pipefail
BASE=${BASE:-http://127.0.0.1:8080}

call() {  # call METHOD PATH EXPECTED_STATUS [JSON]; prints the response body
    local out
    out=$(curl -sS -w '\n%{http_code}' -X "$1" "$BASE$2" -H 'Content-Type: application/json' ${4:+-d "$4"})
    [[ ${out##*$'\n'} == "$3" ]] || { echo "FAIL $1 $2: expected $3, got: $out" >&2; exit 1; }
    echo "ok   $1 $2 -> $3" >&2
    echo "${out%$'\n'*}"
}

LAT=$((RANDOM % 120 - 60)).$RANDOM LON=$((RANDOM % 340 - 170)).$RANDOM PROMO=SMOKE$RANDOM$RANDOM
call GET /version 200
DRIVER=$(call POST /drivers 201 '{"name":"Smoke","vehicle":{"model":"Nexon EV","connector_types":["AC","DC"]}}' | jq .id)
call POST /stations 201 "{\"name\":\"Smoke\",\"lat\":$LAT,\"lon\":$LON,\"connectors\":[{\"type\":\"AC\",\"power_kw\":7.4}]}" >/dev/null
call POST /promos 201 "{\"code\":\"$PROMO\",\"percent_off\":10}" >/dev/null
START="{\"driver_id\":$DRIVER,\"lat\":$LAT,\"lon\":$LON,\"radius_km\":1,\"connector_type\":\"AC\""
SESSION=$(call POST /sessions 201 "$START,\"promo_code\":\"$PROMO\"}" | jq .id)
call POST /sessions 409 "$START}" >/dev/null  # one active session per driver
call POST "/sessions/$SESSION/end" 200 '{"energy_kwh":"12.5"}' | jq -e '.status == "COMPLETED" and (.bill.discount | tonumber) > 0' >/dev/null
call GET "/drivers/$DRIVER/sessions" 200 | jq -e '.completed | length == 1' >/dev/null
echo "PASS user journey against $BASE"
