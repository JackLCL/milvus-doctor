#!/usr/bin/env bash
# Deliberate, bounded container-state fixtures. Not a Doctor runtime command.
set -euo pipefail
names=(doctor-test-unhealthy doctor-test-exited doctor-test-oom)
case "${1:-status}" in
  up)
    [[ "${2:-}" == --allow-lab-writes ]] || exit 2
    for name in "${names[@]}"; do
      if docker inspect "$name" >/dev/null 2>&1; then
        echo "Refusing to overwrite existing container $name" >&2
        exit 2
      fi
    done
    base=(--label io.milvus.doctor.lab=true --memory 32m --memory-swap 32m
      --cpus 0.2 --pids-limit 32 --log-opt max-size=1m --log-opt max-file=1)
    docker run -d --name doctor-test-unhealthy "${base[@]}" \
      --health-cmd=/bin/false --health-interval=2s --health-timeout=1s --health-retries=1 \
      --entrypoint /bin/sh milvusdb/milvus:v2.6.17 -c 'sleep 3600'
    docker run -d --name doctor-test-exited "${base[@]}" \
      --entrypoint /bin/sh milvusdb/milvus:v2.6.17 -c 'exit 42'
    docker run -d --name doctor-test-oom "${base[@]}" \
      --entrypoint /bin/bash milvusdb/milvus:v2.6.17 \
      -c 'data=x; while :; do data=$data$data; done'
    ;;
  down)
    [[ "${2:-}" == --allow-lab-writes ]] || exit 2
    for name in "${names[@]}"; do
      owner="$(docker inspect -f '{{index .Config.Labels "io.milvus.doctor.lab"}}' "$name")"
      [[ "$owner" == true ]] || { echo "Ownership label missing on $name; refusing cleanup." >&2; exit 2; }
    done
    docker rm -f "${names[@]}"
    ;;
  status)
    for name in "${names[@]}"; do
      docker inspect -f '{{.Name}} state={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}{{if .State.Health}} health={{.State.Health.Status}}{{end}}' "$name"
    done
    ;;
  *) echo 'Usage: docker-faults.sh up|down --allow-lab-writes | status'; exit 2 ;;
esac
