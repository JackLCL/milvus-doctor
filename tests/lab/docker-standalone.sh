#!/usr/bin/env bash
# Integration-test infrastructure only. Never called by the Doctor skill.
set -euo pipefail
lab_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
name=doctor-test-embedded
case "${1:-status}" in
  up)
    [[ "${2:-}" == --allow-lab-writes ]] || { echo 'Require up --allow-lab-writes'; exit 2; }
    if docker container inspect "$name" >/dev/null 2>&1; then
      echo "Container $name already exists; refusing to overwrite it." >&2
      exit 2
    fi
    docker run -d --name "$name" --label io.milvus.doctor.lab=true \
      --memory 2g --cpus 2 --pids-limit 512 \
      --log-opt max-size=10m --log-opt max-file=2 \
      -e ETCD_USE_EMBED=true -e ETCD_DATA_DIR=/var/lib/milvus/etcd \
      -e ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml \
      -e COMMON_STORAGETYPE=local -e DEPLOY_MODE=STANDALONE \
      --mount "type=bind,source=$lab_dir/embed-etcd.yaml,target=/milvus/configs/embedEtcd.yaml,readonly" \
      --mount "type=bind,source=$lab_dir/user.yaml,target=/milvus/configs/user.yaml,readonly" \
      -p 127.0.0.1:29530:19530 -p 127.0.0.1:29091:9091 \
      --health-cmd='curl -fsS http://localhost:9091/healthz' \
      --health-interval=10s --health-start-period=60s --health-timeout=3s \
      milvusdb/milvus:v2.6.17 milvus run standalone
    ;;
  down)
    [[ "${2:-}" == --allow-lab-writes ]] || { echo 'Require down --allow-lab-writes'; exit 2; }
    owner="$(docker inspect -f '{{index .Config.Labels "io.milvus.doctor.lab"}}' "$name")"
    [[ "$owner" == true ]] || { echo 'Ownership label missing; refusing cleanup.' >&2; exit 2; }
    docker rm -f "$name"
    ;;
  status) docker ps -a --filter "name=^/${name}$" --format '{{.Names}} {{.Status}} {{.Ports}}' ;;
  *) echo 'Usage: docker-standalone.sh up|down --allow-lab-writes | status'; exit 2 ;;
esac
