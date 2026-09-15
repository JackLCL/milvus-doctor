#!/usr/bin/env bash
# Dedicated disposable authentication lab; explicitly write-capable setup only.
set -euo pipefail
lab_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
name=doctor-test-auth
case "${1:-status}" in
  up)
    [[ "${2:-}" == --allow-lab-writes ]] || exit 2
    if docker inspect "$name" >/dev/null 2>&1; then
      echo "Refusing to overwrite existing container $name" >&2
      exit 2
    fi
    docker run -d --name "$name" --label io.milvus.doctor.lab=true \
      --memory 2g --cpus 2 --pids-limit 512 \
      --log-opt max-size=10m --log-opt max-file=2 \
      -e ETCD_USE_EMBED=true -e ETCD_DATA_DIR=/var/lib/milvus/etcd \
      -e ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml \
      -e COMMON_STORAGETYPE=local -e DEPLOY_MODE=STANDALONE \
      --mount "type=bind,source=$lab_dir/embed-etcd.yaml,target=/milvus/configs/embedEtcd.yaml,readonly" \
      --mount "type=bind,source=$lab_dir/auth-user.yaml,target=/milvus/configs/user.yaml,readonly" \
      -p 127.0.0.1:30530:19530 -p 127.0.0.1:30091:9091 \
      --health-cmd='curl -fsS http://localhost:9091/healthz' \
      --health-interval=10s --health-start-period=60s --health-timeout=3s \
      milvusdb/milvus:v2.6.17 milvus run standalone
    ;;
  down)
    [[ "${2:-}" == --allow-lab-writes ]] || exit 2
    owner="$(docker inspect -f '{{index .Config.Labels "io.milvus.doctor.lab"}}' "$name")"
    [[ "$owner" == true ]] || { echo 'Ownership label missing; refusing cleanup.' >&2; exit 2; }
    docker rm -f "$name"
    ;;
  status) docker ps -a --filter "name=^/${name}$" --format '{{.Names}} {{.Status}} {{.Ports}}' ;;
  *) echo 'Usage: docker-auth.sh up|down --allow-lab-writes | status'; exit 2 ;;
esac
