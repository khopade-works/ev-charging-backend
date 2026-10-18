#!/usr/bin/env bash
# Creates the kind cluster, the monitoring stack and the DB secret. Idempotent; the password is random and never printed.
set -euo pipefail
cd "$(dirname "$0")/.."

kind get clusters | grep -qx ev || kind create cluster --config deploy/kind/cluster.yaml
kubectl config use-context kind-ev
for ns in ev monitoring; do kubectl create namespace $ns --dry-run=client -o yaml | kubectl apply -f -; done
kubectl label namespace ev pod-security.kubernetes.io/enforce=restricted --overwrite  # rejects privileged pods
kubectl -n ev get secret ev-charging-db >/dev/null 2>&1 ||
  kubectl -n ev create secret generic ev-charging-db --from-literal=password="$(openssl rand -hex 24)"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm upgrade --install kps prometheus-community/kube-prometheus-stack --version 92.10.1 -n monitoring \
  -f deploy/monitoring/kube-prometheus-stack.values.yaml --wait --timeout 10m
kubectl apply -f deploy/monitoring/jaeger.yaml
