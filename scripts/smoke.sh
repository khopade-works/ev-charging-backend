#!/usr/bin/env bash
# User journey: register a driver and a station -> start a session -> end it with a bill -> history. Exits non-zero on failure.
set -euo pipefail
BASE=${BASE:-http://127.0.0.1:8080}

call() {  # call METHOD PATH EXPECTED_STATUS [JSON]; prints the response body
    local out
    out=$(curl -sS -w '\n%{http_code}' -X "$1" "$BASE$2" -H 'Content-Type: application/json' ${4:+-d "$4"})
    [[ ${out##*$'\n'} == "$3" ]] || { echo "FAIL $1 $2: expected $3, got: $out" >&2; exit 1; }
    echo "ok   $1 $2 -> $3" >&2
    echo "${out%$'\n'*}"
}

LAT=$((RANDOM % 120 - 60)).$RANDOM LON=$((RANDOM % 340 - 170)).$RANDOM  # a fresh spot: safe on a non-empty database
DRIVER=$(call POST /drivers 201 '{"name":"Smoke","vehicle":{"model":"Nexon EV","connector_types":["AC"]}}' | jq .id)
call POST /stations 201 "{\"name\":\"Smoke\",\"lat\":$LAT,\"lon\":$LON,\"connectors\":[{\"type\":\"AC\",\"power_kw\":7.4}]}" >/dev/null
SESSION=$(call POST /sessions 201 "{\"driver_id\":$DRIVER,\"lat\":$LAT,\"lon\":$LON,\"radius_km\":1,\"connector_type\":\"AC\"}" | jq .id)
call POST "/sessions/$SESSION/end" 200 '{"energy_kwh":"12.5"}' | jq -e '.status == "COMPLETED"' >/dev/null
call GET "/drivers/$DRIVER/sessions" 200 | jq -e '.completed | length == 1' >/dev/null
echo "PASS user journey against $BASE"
