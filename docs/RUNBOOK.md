# Runbook

What "healthy" means, how each alert maps to an action, and how to prove recovery. Commands assume the local kind cluster (`kubectl config use-context kind-ev`).

## What healthy means

| Signal | Healthy | Where |
|---|---|---|
| Availability SLO | ≥ 99.5% of API requests are not 5xx (probes excluded) | Grafana "EV Charging" → 5xx ratio vs SLO; recording rule `ev:http_availability:ratio_rate5m` |
| Latency | p95 < 300 ms | Grafana latency panel; `EvChargingHighLatency` |
| User success | session starts succeed; rejections are business outcomes (no free connector), not errors | `ev_charging_sessions_started_total` vs `ev_charging_session_start_rejected_total{reason}` |
| Capacity to serve | 2/2 API pods Ready, Postgres Ready | `kubectl get pods -n ev`; dashboard "Ready API replicas" / "DB ready" |
| Right version | every pod reports the deployed git sha | `curl -s 127.0.0.1:8080/version`; `ev_build_info` panel; `helm history ev -n ev` |
| End to end | the user journey passes | `BASE=http://127.0.0.1:8080 scripts/smoke.sh` |

**Verify recovery** after any action: all pods Ready, `/version` returns the expected sha, `scripts/smoke.sh` prints `PASS`, and the 5xx ratio panel returns to 0 for 5 minutes (the fast-burn alert resolves once its 5m window is clean).

## Alerts

Every alert has `runbook_url` pointing here. Severity `critical` = users are failing now; `warning` = budget or quality at risk.

### EvChargingErrorBudgetBurnFast

5xx ratio above 14.4 × 0.5% over both 5m and 1h: at this rate 2% of a 30-day error budget goes in an hour.

1. `kubectl get pods -n ev` and `helm history ev -n ev --max 3`. Did a release just happen? If yes, roll back first, investigate second: `helm rollback ev -n ev --wait` (goes to the previous revision).
2. Otherwise find the failing route: Grafana request-rate panel by route/status, then logs: `kubectl logs -n ev -l app.kubernetes.io/component=api --since=15m | jq 'select(.status>=500)'`.
3. Take a `trace_id` from a failing log line and open it in Jaeger (http://127.0.0.1:16686) to see whether the time or the error is in Postgres.
4. Verify recovery (above).

### EvChargingErrorBudgetBurnSlow

5xx ratio above 6 × 0.5% over 30m and 6h. Same investigation as the fast burn, without rolling back by reflex: look for a pattern (one route, one input) and fix forward with a normal release.

### EvChargingHighLatency

p95 above 300 ms for 10 minutes. Every service call takes one Postgres advisory lock, so latency usually means lock wait or a slow database:

1. Dashboard "Store transaction p95": if it tracks request latency, requests are queueing on the lock (load) or Postgres is slow.
2. Jaeger: open a slow trace; the `SELECT pg_advisory_xact_lock` span shows lock wait, other SQL spans show query time.
3. `kubectl describe pod -n ev ev-charging-db-0`: restarts or `OOMKilled` point at the database's 512Mi limit.
4. Adding API replicas does **not** raise write throughput (they share the lock). The fix is the row-level claim described in `ev/pgstore.py`.

### EvChargingNoReadyPods

No API pod has been Available for 1 minute: the app is down for everyone.

1. `kubectl get pods -n ev` and `kubectl describe pod -n ev -l app.kubernetes.io/component=api`.
2. `CrashLoopBackOff` with `invalid EV_...` in the logs = bad configuration: roll back (`helm rollback ev -n ev --wait`).
3. Pods `Running` but `0/1` Ready = readiness fails = the database is unreachable: see the next alert.

### EvChargingDatabaseDown

The Postgres StatefulSet has no ready replica. API pods turn NotReady (readiness checks the database), so the Service stops routing to them.

1. `kubectl get pods,pvc -n ev` and `kubectl logs -n ev ev-charging-db-0 --previous`.
2. A deleted or crashed pod is recreated by the StatefulSet on the same PVC: data is kept. Wait for `1/1`, then the API pods turn Ready by themselves (their connection pool replaces dead connections).
3. PVC `Pending` or lost = data problem: stop and restore (see Limitations in the README: there is no backup job).
4. Verify recovery, including that old data is still there: `curl -s 127.0.0.1:8080/drivers/1/sessions`.

### EvChargingSessionStartRejectionsHigh

More than half of session starts were rejected over 10 minutes. These are 409/422 business outcomes, so they do not burn the availability SLO, but users are not getting to charge.

1. `sum by (reason) (rate(ev_charging_session_start_rejected_total[10m]))` in Prometheus.
2. `NoConnectorAvailable` = demand exceeds free connectors (or connectors left out of service); `InvalidInput` = a client sending bad data (e.g. deleted promo); `Conflict` = drivers retrying while already charging.

## Releases

| Task | Command |
|---|---|
| Deploy a commit (gated on its green CI run) | `scripts/deploy.sh <sha>` |
| What is running, and from which CI run | `helm history ev -n ev` (description holds `sha=… ci=…`), `curl 127.0.0.1:8080/version` |
| Roll back to the previous release | `helm rollback ev -n ev --wait` |
| Roll back to a specific release | `helm rollback ev <revision> -n ev --wait` |
| Detect drift (live objects edited by hand) | `helm get manifest ev -n ev \| kubectl diff -n ev -f -` (exit 0 = no drift) |

A release that never turns Ready (crash, failing readiness) is rolled back automatically by `--rollback-on-failure`; with `maxUnavailable: 0` the old pods keep serving throughout. A release that is Ready but wrong (bad behaviour) is caught by the smoke test and alerts, and rolled back by hand.
