# Evidence

All evidence was captured on 2026-10-18 against **application version [`22ba208`](https://github.com/khopade-works/ev-charging-backend/commit/22ba20890315ff15e08084556a4ff204954c21c4)** (image `ghcr.io/khopade-works/ev-charging-backend:22ba20890315ff15e08084556a4ff204954c21c4`, digest `sha256:c49c4b78…`). The later commits in the PR change only `docs/`, `README.md` and two lines of `scripts/deploy.sh`; check with `git diff 22ba208 --stat -- ev tests deploy Dockerfile .github`. Each file holds the exact commands and their raw output.

| File | Shows | Rubric area |
|---|---|---|
| [00-ci-run.txt](00-ci-run.txt) | [CI run 39148868151](https://github.com/khopade-works/ev-charging-backend/actions/runs/39148868151) for this sha, every job and step green: ruff, pytest (111 on memory, 113 on Postgres), Trivy fs, multi-arch build to GHCR, Trivy image, and e2e on kind (`/version` equals the sha, user journey passes, bad release rolled back) | CI/CD, security checks |
| [01-tests-and-mutation.txt](01-tests-and-mutation.txt) | Both test suites pass. With the advisory lock removed, exactly the two 2-replica race cases fail, which proves the lock is what stops double-booking across replicas. | Verification |
| [02-deploy.txt](02-deploy.txt) | `deploy.sh` refuses a commit with no green CI run. It then promotes `22ba208`: a rolling update (old pods terminating while new ones serve), a `helm history` entry recording `sha=… ci=<run URL>`, a pod label and image digest that match the sha, `/version` equal to the sha, and the user journey passing. | Release, traceability |
| [03-observability.txt](03-observability.txt) | Prometheus scraping both pods through the PodMonitor; request, latency and user-success metrics; the running image per pod; all 3 alert rules loaded and healthy; JSON logs with trace ids; one Jaeger trace with HTTP and SQL spans; the Grafana dashboard present | Observability |
| [04-security-and-recovery.txt](04-security-and-recovery.txt) | **Security:** PSA `restricted` rejects a privileged pod; the app runs as uid 10001 on a read-only root filesystem with no pip; `/metrics` is not on the public port; the NetworkPolicy blocks a non-API pod from Postgres while the API pod connects; no committed credentials. **Fault 1, database down:** readiness returns 503, both pods leave the Service, and clients get no response rather than 500s. After recovery there are 0 restarts and pre-outage data is kept. **Fault 2, bad release:** `EV_PEAK=bogus` never becomes Ready, Helm rolls back automatically, `/version` stays on the good sha and the user journey passes. | Kubernetes operation, security, reliability |

**Limitations of this evidence:**
- The rate values in `03` come from pods that were only a few minutes old, so `increase()` is extrapolated over a short window.
- No alert was driven into the firing state. The rules are shown as loaded and healthy, and the response to each one is in the [runbook](../RUNBOOK.md).
- The local cluster is the only "production"; there is no staging.
