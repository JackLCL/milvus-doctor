#!/usr/bin/env python3
"""Create a short-lived SA-only test kubeconfig without printing credentials.

This test setup performs TokenRequest; the Doctor runtime never calls it.
"""
import argparse
import json
import os
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-lab-writes", action="store_true", required=True)
    args = parser.parse_args()
    base = ["kubectl", "--kubeconfig", args.source, "--request-timeout=15s"]
    original = json.loads(subprocess.check_output(
        base + ["config", "view", "--raw", "--minify", "--flatten", "-o", "json"],
        text=True,
    ))
    token = subprocess.check_output(
        base + ["-n", "doctor-test-helm", "create", "token", "doctor-test-reader", "--duration=2h"],
        text=True,
    ).strip()
    config = {
        "apiVersion": "v1", "kind": "Config", "current-context": "doctor-test-reader",
        "clusters": [{"name": "doctor-test-cluster", "cluster": original["clusters"][0]["cluster"]}],
        "contexts": [{"name": "doctor-test-reader", "context": {
            "cluster": "doctor-test-cluster", "user": "doctor-test-reader", "namespace": "doctor-test-helm",
        }}],
        "users": [{"name": "doctor-test-reader", "user": {"token": token}}],
    }
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as target:
        json.dump(config, target)
    print("Created a two-hour namespace-only reader kubeconfig; no admin credentials copied.")


if __name__ == "__main__":
    main()
