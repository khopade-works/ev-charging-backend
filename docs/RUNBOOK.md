# Runbook

Commands assume `kubectl config use-context kind-ev`. **Recovery is verified when** all pods are Ready, `curl -s 127.0.0.1:8080/version` returns the expected sha, `scripts/smoke.sh` prints `PASS`, and the 5xx ratio on the Grafana "EV Charging" dashboard has been back at 0 for 5 minutes.

## EvChargingErrorBudgetBurn

The 5xx ratio is above 14.4 × 0.5% over both 1h and 5m. At this rate, 2% of a 30-day error budget is spent per hour.

1. Check whether a release just happened: `helm history ev -n ev --max 3`. If one did, roll back first and investigate second: `helm rollback ev -n ev --wait`.
2. Find the failing route on the dashboard, then read its logs: `kubectl logs -n ev -l app.kubernetes.io/component=api --since=15m | jq 'select(.status >= 500)'`.
3. Open the log line's `trace_id` in Jaeger (http://127.0.0.1:16686). It shows whether the error or the time is in Postgres.

## EvChargingNoAvailablePods

No API pod has been available for 1 minute, so the API is down for everyone. The error-ratio alert can't fire here, because no pod is receiving requests.

1. Run `kubectl get pods -n ev`.
2. If a pod is in `CrashLoopBackOff` and `kubectl logs` shows a `KeyError` on `EV_PEAK`/`EV_STRATEGY`, the configuration is bad: `helm rollback ev -n ev --wait`.
3. If the pods are `Running` but not Ready, readiness is failing because the database is unreachable. Run `kubectl get pods,pvc -n ev` and `kubectl logs -n ev ev-charging-db-0`. The StatefulSet recreates the database pod on the same PVC, so the data is kept, and the API turns Ready again without a restart.

## EvChargingSessionStartsRejected

More than half of session starts have been rejected for 10 minutes. These are 409/422 business answers rather than errors, but drivers are not getting to charge.

1. In Prometheus, run `sum by (reason) (rate(ev_charging_session_start_rejected_total[10m]))` to see why.
2. `NoConnectorAvailable` means demand is above the free connectors, or connectors were left out of service. `InvalidInput` means clients are sending bad data, such as a deleted promo. `Conflict` means drivers are retrying while already charging.

## Releases

| Task | Command |
|---|---|
| Deploy a commit (gated on its green CI run) | `scripts/deploy.sh <sha>` |
| What is running, and from which CI run | `helm history ev -n ev` (the description is `sha=… ci=…`), `curl 127.0.0.1:8080/version` |
| Roll back | `helm rollback ev [revision] -n ev --wait` |
| Detect drift | `helm get manifest ev -n ev \| kubectl diff -n ev -f -` (exit 0 means no drift) |
