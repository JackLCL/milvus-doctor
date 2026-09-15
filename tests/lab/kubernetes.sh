#!/usr/bin/env bash
# Explicit write-capable integration-test setup; not part of the skill runtime.
set -euo pipefail
lab_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ -n "${DOCTOR_LAB_KUBECONFIG:-}" ]] || { echo 'Set DOCTOR_LAB_KUBECONFIG to an explicit test kubeconfig.' >&2; exit 2; }
args=(--kubeconfig "$DOCTOR_LAB_KUBECONFIG")
case "${1:-status}" in
  helm-up)
    [[ "${2:-}" == --allow-lab-writes ]] || exit 2
    [[ -n "${DOCTOR_LAB_CHART:-}" ]] || { echo 'Set DOCTOR_LAB_CHART to the official Milvus 5.0.19 chart directory.' >&2; exit 2; }
    if kubectl "${args[@]}" get namespace doctor-test-helm >/dev/null 2>&1; then
      echo 'doctor-test-helm already exists; refusing to adopt or overwrite it.' >&2
      exit 2
    fi
    helm "${args[@]}" install doctor-test-helm "$DOCTOR_LAB_CHART" \
      -f "$lab_dir/helm-values.yaml" --namespace doctor-test-helm --create-namespace \
      --wait --timeout 300s
    kubectl "${args[@]}" label namespace doctor-test-helm io.milvus.doctor.lab=true
    kubectl "${args[@]}" create -f "$lab_dir/rbac.yaml"
    ;;
  operator-up)
    [[ "${2:-}" == --allow-lab-writes ]] || exit 2
    kubectl "${args[@]}" get crd milvuses.milvus.io >/dev/null
    kubectl "${args[@]}" create -f "$lab_dir/operator-namespace.yaml"
    kubectl "${args[@]}" create -f "$lab_dir/operator-standalone.yaml"
    ;;
  faults-up)
    [[ "${2:-}" == --allow-lab-writes ]] || exit 2
    if kubectl "${args[@]}" get namespace doctor-test-faults >/dev/null 2>&1; then
      echo 'doctor-test-faults already exists; refusing to adopt it.' >&2
      exit 2
    fi
    kubectl "${args[@]}" create -f "$lab_dir/kubernetes-faults.yaml"
    ;;
  status)
    for namespace in doctor-test-helm doctor-test-operator doctor-test-faults; do
      kubectl "${args[@]}" -n "$namespace" get pods,deployments,statefulsets,pvc
    done
    ;;
  *) echo 'Usage: kubernetes.sh helm-up|operator-up|faults-up --allow-lab-writes | status'; exit 2 ;;
esac
