#!/usr/bin/env bash
# Creates the kind cluster, the monitoring stack and the secrets. Idempotent; secrets are random and never printed.
set -euo pipefail
cd "$(dirname "$0")/.."

kind get clusters | grep -qx ev || kind create cluster --config deploy/kind/cluster.yaml
kubectl config use-context kind-ev
kubectl apply -f deploy/k8s/namespace.yaml
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl -n ev get secret ev-charging-db >/dev/null 2>&1 ||
  kubectl -n ev create secret generic ev-charging-db --from-literal=password="$(openssl rand -hex 24)"
kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1 ||
  kubectl -n monitoring create secret generic grafana-admin \
    --from-literal=admin-user=admin --from-literal=admin-password="$(openssl rand -hex 16)"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm upgrade --install kps prometheus-community/kube-prometheus-stack --version 92.10.1 -n monitoring \
  -f deploy/monitoring/kube-prometheus-stack.values.yaml --wait --timeout 10m
kubectl apply -f deploy/monitoring/jaeger.yaml
