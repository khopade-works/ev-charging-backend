import pytest
from fastapi.testclient import TestClient

from ev.api import create_app
from ev.service import ChargingService
from ev.store import InMemoryStore

DRIVER = ("POST", "/drivers", {"name": "Asha", "vehicle": {"model": "Nexon EV", "connector_types": ["AC", "DC"]}})
STATION_BODY = {
    "name": "Koramangala",
    "lat": 12.9352,
    "lon": 77.6245,
    "connectors": [{"type": "AC", "power_kw": 7.4}, {"type": "DC", "power_kw": 50}],
}
STATION = ("POST", "/stations", STATION_BODY)
PROMO = ("POST", "/promos", {"code": "WELCOME10", "percent_off": 10})
START_BODY = {"driver_id": 1, "lat": 12.9340, "lon": 77.6230, "radius_km": 5, "connector_type": "AC"}
START = ("POST", "/sessions", START_BODY)
END = ("POST", "/sessions/1/end", {"energy_kwh": "12.5"})


def client() -> TestClient:
    return TestClient(create_app(ChargingService(InMemoryStore())))


def test_api_happy_path():
    c = client()
    assert c.post("/drivers", json=DRIVER[2]).status_code == 201
    assert c.post("/stations", json=STATION_BODY).status_code == 201
    assert c.post("/promos", json=PROMO[2]).status_code == 201
    assert c.post("/stations/1/connectors/1/out-of-service").status_code == 200

    r = c.post("/sessions", json={**START_BODY, "promo_code": "WELCOME10"})
    assert r.status_code == 201
    assert r.json()["connector_type"] == "DC"
    assert r.json()["billed_type"] == "AC"

    r = c.post("/sessions/1/end", json={"energy_kwh": "12.5"})
    assert r.status_code == 200
    assert r.json()["bill"]["total"] == "157.50"

    assert len(c.get("/drivers/1/sessions").json()["completed"]) == 1
    assert c.get("/stations/1/sessions").json()["active"] == []
    assert c.delete("/promos/WELCOME10").status_code == 204


@pytest.mark.parametrize(
    "setup, request_, status, error",
    [
        ([], END, 404, "NotFound"),
        ([], ("GET", "/drivers/1/sessions", None), 404, "NotFound"),
        ([DRIVER], START, 409, "NoConnectorAvailable"),
        ([DRIVER, STATION, START, END], END, 409, "Conflict"),
        ([DRIVER, STATION], ("POST", "/sessions", {**START_BODY, "promo_code": "NOPE"}), 422, "InvalidInput"),
        ([], ("POST", "/stations", {**STATION_BODY, "connectors": []}), 422, "InvalidInput"),
        ([DRIVER, STATION, START], ("POST", "/sessions/1/end", {"energy_kwh": -1}), 422, "InvalidInput"),
        ([], ("POST", "/stations", {**STATION_BODY, "lat": 91}), 422, "InvalidInput"),
        ([DRIVER, STATION], ("POST", "/sessions", {**START_BODY, "radius_km": 0}), 422, "InvalidInput"),
        ([PROMO], PROMO, 409, "Conflict"),
        ([], ("DELETE", "/promos/NOPE", None), 404, "NotFound"),
        ([], ("POST", "/drivers", {"name": "Asha"}), 422, None),
    ],
)
def test_api_error_mapping(setup, request_, status, error):
    c = client()
    for method, path, body in setup:
        assert c.request(method, path, json=body).is_success
    method, path, body = request_
    r = c.request(method, path, json=body)
    assert r.status_code == status
    if error is None:
        assert "detail" in r.json()
    else:
        assert r.json()["error"] == error
