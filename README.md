# EV Charging Network — backend

Backend for an electric-vehicle charging network: register drivers and stations, start a session on a free compatible connector within a radius, end it with the energy delivered, and bill it with a tiered AC/DC tariff and promo codes.

The design, assumptions and test plan are in [PLAN.md](PLAN.md). This README grows with the implementation.

## Setup

```bash
uv venv -p 3.14 .venv
uv pip install --python .venv/bin/python -r pyproject.toml --extra test
.venv/bin/pytest -q
```
