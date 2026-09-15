#!/usr/bin/env python3
"""Build and verify minimal Milvus metadata credentials in doctor-test-auth ONLY.

This lab test intentionally makes denied write requests against disposable test
collections. It is not imported or called by the read-only Doctor skill.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys


URI = "http://127.0.0.1:30530"
COLLECTION = "doctor_test_metadata"
WRITE_PROBE = "doctor_test_write_probe"
USER = "doctor_test_reader"
ROLE = "doctor_test_metadata_reader"


def require_admin_token():
    token = os.environ.get("DOCTOR_LAB_ADMIN_TOKEN", "")
    if not token.strip():
        raise ValueError("Set DOCTOR_LAB_ADMIN_TOKEN for the dedicated test instance")
    return token


def verify_owned_container(container):
    if container.get("Config", {}).get("Labels", {}).get("io.milvus.doctor.lab") != "true":
        raise ValueError("Not an owned test container")
    bindings = container.get("NetworkSettings", {}).get("Ports", {}).get("19530/tcp", [])
    if bindings != [{"HostIp": "127.0.0.1", "HostPort": "30530"}]:
        raise ValueError("Test endpoint must have only the expected loopback binding")
    if container.get("Config", {}).get("Image") != "milvusdb/milvus:v2.6.17":
        raise ValueError("Unexpected lab image")


def write_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--allow-lab-writes", required=True, action="store_true")
    args = parser.parse_args()
    try:
        admin_token = require_admin_token()
    except ValueError as exc:
        parser.error(str(exc))
    container = json.loads(subprocess.check_output(
        ["docker", "inspect", "doctor-test-auth"], text=True, stderr=subprocess.DEVNULL,
    ))[0]
    verify_owned_container(container)
    output = Path(args.output_dir)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Expected SDK permission failures must not dump token-bearing diagnostics.
    logging.disable(logging.CRITICAL)
    from pymilvus import MilvusClient
    admin = MilvusClient(uri=URI, token=admin_token, timeout=10)
    report = {"target": "doctor-test-auth", "server_version": admin.get_server_version(),
              "grants": [], "reads": [], "denials": [], "write_scope": [COLLECTION, WRITE_PROBE]}
    if COLLECTION in admin.list_collections() or WRITE_PROBE in admin.list_collections():
        raise ValueError("Lab collection already exists; refusing to adopt it")
    if USER in admin.list_users():
        raise ValueError("Lab user already exists; refusing to change it")
    admin.create_collection(collection_name=COLLECTION, dimension=2)
    admin.create_role(role_name=ROLE)
    password = secrets.token_urlsafe(24)
    admin.create_user(user_name=USER, password=password)
    admin.grant_role(user_name=USER, role_name=ROLE)
    planned = [("ShowCollections", "*"), ("DescribeCollection", COLLECTION),
               ("GetStatistics", COLLECTION), ("GetLoadState", COLLECTION),
               ("IndexDetail", COLLECTION)]
    for privilege, collection in planned:
        try:
            admin.grant_privilege_v2(role_name=ROLE, privilege=privilege,
                                     collection_name=collection, db_name="default")
            report["grants"].append({"privilege": privilege, "scope": collection, "status": "granted"})
        except Exception as exc:
            report["grants"].append({"privilege": privilege, "scope": collection,
                                     "status": "unsupported", "error_type": type(exc).__name__})
    token = USER + ":" + password
    write_private(output / "reader-token", token)
    reader = MilvusClient(uri=URI, token=token, timeout=10)
    checks = [
        ("get_server_version", lambda: reader.get_server_version()),
        ("list_collections", lambda: reader.list_collections()),
        ("describe_collection", lambda: reader.describe_collection(collection_name=COLLECTION)),
        ("get_collection_stats", lambda: reader.get_collection_stats(collection_name=COLLECTION)),
        ("get_load_state", lambda: reader.get_load_state(collection_name=COLLECTION)),
        ("list_indexes", lambda: reader.list_indexes(collection_name=COLLECTION)),
        ("describe_index", lambda: reader.describe_index(collection_name=COLLECTION, index_name="vector")),
    ]
    for name, call in checks:
        try:
            call()
            report["reads"].append({"operation": name, "status": "allowed"})
        except Exception as exc:
            report["reads"].append({"operation": name, "status": "unavailable", "error_type": type(exc).__name__})
    # All names are exclusively created by this test in the owned auth container.
    probes = [
        ("create_collection", lambda: reader.create_collection(collection_name=WRITE_PROBE, dimension=2)),
        ("insert", lambda: reader.insert(collection_name=COLLECTION, data=[{"id": 1, "vector": [0.1, 0.2]}])),
        ("drop_collection", lambda: reader.drop_collection(collection_name=COLLECTION)),
        ("query_business_data", lambda: reader.query(collection_name=COLLECTION, filter="id >= 0", output_fields=["id"])),
    ]
    for name, call in probes:
        try:
            call()
            report["denials"].append({"operation": name, "status": "UNEXPECTEDLY_ALLOWED"})
        except Exception as exc:
            message = str(exc)
            denied = bool(re.search(r"permission.{0,30}(?:deny|denied)|not authorized|authorization", message, re.I))
            code = getattr(exc, "code", "")
            code = code() if callable(code) else code
            report["denials"].append({"operation": name, "status": "denied" if denied else "inconclusive",
                                       "error_type": type(exc).__name__, "code": str(code)})
    report["collection_preserved"] = COLLECTION in admin.list_collections()
    report["row_count_after_denied_writes"] = admin.get_collection_stats(collection_name=COLLECTION).get("row_count") if report["collection_preserved"] else None
    report["write_probe_not_created"] = WRITE_PROBE not in admin.list_collections()
    report["success"] = (all(x["status"] == "allowed" for x in report["reads"])
                          and all(x["status"] == "denied" for x in report["denials"])
                          and report["collection_preserved"] and report["write_probe_not_created"])
    write_private(output / "verification.json", json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    reader.close()
    admin.close()
    return 0 if report["success"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # SDK exceptions can include credentials. Do not print their text or a traceback.
        print("Lab verification failed (" + type(exc).__name__ + "). Check the dedicated test setup.", file=sys.stderr)
        raise SystemExit(1)
