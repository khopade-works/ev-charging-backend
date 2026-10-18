#!/usr/bin/env bash
# Idempotent: creates the kind cluster, monitoring stack and secrets. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

KPS_VERSION=92.10.1

if ! kind get clusters | grep -qx ev; then
  kind create cluster --config deploy/kind/cluster.yaml
fi
kubectl config use-context kind-ev

kubectl apply -f deploy/k8s/namespace.yaml
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

if ! kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n monitoring create secret generic grafana-admin \
    --from-literal=admin-user=admin \
    --from-literal=admin-password="$(openssl rand -hex 16)"
fi

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm upgrade --install kps prometheus-community/kube-prometheus-stack --version "$KPS_VERSION" \
  -n monitoring -f deploy/monitoring/kube-prometheus-stack.values.yaml --wait --timeout 10m

kubectl apply -f deploy/monitoring/jaeger.yaml
kubectl -n monitoring rollout status deploy/jaeger --timeout=3m

if ! kubectl -n ev get secret ev-charging-db >/dev/null 2>&1; then
  kubectl -n ev create secret generic ev-charging-db --from-literal=password="$(openssl rand -hex 24)"
fi

cat <<MSG
Cluster ready.
  App (after helm install): http://127.0.0.1:8080
  Grafana:    http://127.0.0.1:3000  (user admin)
  Prometheus: http://127.0.0.1:9090
  Jaeger UI:  http://127.0.0.1:16686
Grafana password:
  kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d
MSG
