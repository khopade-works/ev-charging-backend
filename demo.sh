#!/usr/bin/env bash
# Needs a FRESH server (ids are hardcoded and only valid on an empty store):
#   EV_PEAK=none .venv/bin/uvicorn ev.api:app
# Usage: ./demo.sh [seed|all]   (default: all = seed + walkthrough)
set -euo pipefail

BASE=${BASE:-http://127.0.0.1:8000}

call() {  # call "header" METHOD PATH [JSON]
    echo "== $1"
    echo "> $2 $3 ${4:-}"
    curl -s -X "$2" "$BASE$3" -H 'Content-Type: application/json' ${4:+-d "$4"} -w '\n[HTTP %{http_code}]\n'
    echo
}

HERE='"lat":12.9340,"lon":77.6230,"radius_km":5'

seed() {
    call "seed: driver 1 Asha (AC+DC)" POST /drivers '{"name":"Asha","vehicle":{"model":"Nexon EV","connector_types":["AC","DC"]}}'
    call "seed: driver 2 Ravi (AC)" POST /drivers '{"name":"Ravi","vehicle":{"model":"Tiago EV","connector_types":["AC"]}}'
    call "seed: station 1 Koramangala (1=AC, 2=DC)" POST /stations '{"name":"Koramangala","lat":12.9352,"lon":77.6245,"connectors":[{"type":"AC","power_kw":7.4},{"type":"DC","power_kw":50}]}'
    call "seed: station 2 Indiranagar (out of range)" POST /stations '{"name":"Indiranagar","lat":12.9784,"lon":77.6408,"connectors":[{"type":"AC","power_kw":7.4}]}'
    call "seed: promo WELCOME10" POST /promos '{"code":"WELCOME10","percent_off":10}'
}

walkthrough() {
    call "1. take AC out -> expect 200, OUT_OF_SERVICE" POST /stations/1/connectors/1/out-of-service
    call "2. Asha starts AC + WELCOME10 -> expect 201, station 1, connector 2, connector_type DC, billed_type AC (fallback)" POST /sessions "{\"driver_id\":1,$HERE,\"connector_type\":\"AC\",\"promo_code\":\"WELCOME10\"}"
    call "3. Ravi starts AC -> expect 409 NoConnectorAvailable" POST /sessions "{\"driver_id\":2,$HERE,\"connector_type\":\"AC\"}"
    call "4. take occupied DC out -> expect 409" POST /stations/1/connectors/2/out-of-service
    call "5. end session 1 at 12.5 kWh -> expect 200, energy_cost 175.00, discount 17.50, total 157.50 (as DC: 211.50)" POST /sessions/1/end '{"energy_kwh":"12.5"}'
    call "6a. return AC to service -> expect 200" POST /stations/1/connectors/1/in-service
    call "6b. Ravi starts AC -> expect 201 on connector 1" POST /sessions "{\"driver_id\":2,$HERE,\"connector_type\":\"AC\"}"
    call "7a. end session 2 at -1 kWh -> expect 422" POST /sessions/2/end '{"energy_kwh":-1}'
    call "7b. end session 2 at 5 kWh -> expect 200, total 100.00 (AC minimum)" POST /sessions/2/end '{"energy_kwh":5}'
    call "8. end session 2 again -> expect 409" POST /sessions/2/end '{"energy_kwh":5}'
    call "9a. Asha's history -> expect split by status" GET /drivers/1/sessions
    call "9b. station 1 history -> expect split by status" GET /stations/1/sessions
    call "10a. delete WELCOME10 -> expect 204" DELETE /promos/WELCOME10
    call "10b. Asha starts AC + WELCOME10 -> expect 422" POST /sessions "{\"driver_id\":1,$HERE,\"connector_type\":\"AC\",\"promo_code\":\"WELCOME10\"}"
}

case "${1:-all}" in
    seed) seed ;;
    all) seed; walkthrough ;;
    *) echo "usage: $0 [seed|all]" >&2; exit 2 ;;
esac
