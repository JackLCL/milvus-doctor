#!/usr/bin/env bash
# Exercise server-side authorization with the short-lived, real SA token.
set -euo pipefail
[[ -n "${DOCTOR_LAB_READER_KUBECONFIG:-}" ]] || { echo 'Set DOCTOR_LAB_READER_KUBECONFIG.' >&2; exit 2; }
args=(--kubeconfig "$DOCTOR_LAB_READER_KUBECONFIG" --request-timeout=15s)
for resource in pods services persistentvolumeclaims events deployments.apps statefulsets.apps milvuses.milvus.io; do
  for verb in get list; do
    kubectl "${args[@]}" -n doctor-test-helm auth can-i "$verb" "$resource" --quiet
    printf 'PASS allowed: %s %s in doctor-test-helm\n' "$verb" "$resource"
  done
done
for verb_resource in 'list secrets' 'get secrets' 'delete pods' 'patch deployments.apps' 'update statefulsets.apps' 'list nodes'; do
  read -r verb resource <<< "$verb_resource"
  if kubectl "${args[@]}" -n doctor-test-helm auth can-i "$verb" "$resource" --quiet; then
    printf 'FAIL unexpectedly allowed: %s %s\n' "$verb" "$resource" >&2
    exit 1
  fi
  printf 'PASS denied: %s %s\n' "$verb" "$resource"
done
for verb_subresource in 'create exec' 'get log'; do
  read -r verb subresource <<< "$verb_subresource"
  if kubectl "${args[@]}" -n doctor-test-helm auth can-i "$verb" pods --subresource="$subresource" --quiet; then
    printf 'FAIL unexpectedly allowed: %s pods/%s\n' "$verb" "$subresource" >&2
    exit 1
  fi
  printf 'PASS denied: %s pods/%s\n' "$verb" "$subresource"
done
if kubectl "${args[@]}" -n default auth can-i list pods --quiet; then
  echo 'FAIL reader can list another namespace' >&2
  exit 1
fi
echo 'PASS denied: list pods in another namespace'
