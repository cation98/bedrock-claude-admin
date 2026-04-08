#!/usr/bin/env python3
"""Pod 내부 파일 스캐너 에이전트.

주기적으로 /home/node/workspace/uploads/ 를 스캔하고
auth-gateway API로 결과를 보고한다.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error

SCAN_DIR = os.environ.get("SCAN_DIR", "/home/node/workspace/uploads")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://auth-gateway:8000")
POD_NAME = os.environ.get("POD_NAME", "")
POD_TOKEN = os.environ.get("SECURE_POD_TOKEN", "")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "60"))  # seconds


def scan_directory(scan_dir):
    """디렉토리를 스캔하여 파일 목록을 반환."""
    files = []
    if not os.path.isdir(scan_dir):
        return files
    for root, dirs, filenames in os.walk(scan_dir):
        for fname in filenames:
            fpath = os.path.join(root, fname)
            try:
                stat = os.stat(fpath)
                files.append({
                    "filename": fname,
                    "file_path": os.path.relpath(fpath, "/home/node/workspace"),
                    "file_size_bytes": stat.st_size,
                    "file_type": os.path.splitext(fname)[1].lstrip('.').lower() or "unknown",
                })
            except OSError:
                continue
    return files


def report_to_gateway(files):
    """스캔 결과를 auth-gateway에 보고."""
    url = f"{GATEWAY_URL}/api/v1/governance/scan-report"
    data = json.dumps({"pod_name": POD_NAME, "files": files}).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "X-Pod-Name": POD_NAME,
        "X-Pod-Token": POD_TOKEN,
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[scanner-agent] Report failed: {e}", file=sys.stderr)
        return None


def main():
    print(f"[scanner-agent] Starting. dir={SCAN_DIR} interval={SCAN_INTERVAL}s")
    while True:
        files = scan_directory(SCAN_DIR)
        if files:
            result = report_to_gateway(files)
            if result:
                print(f"[scanner-agent] Reported {len(files)} files. Response: {result}")
        else:
            print(f"[scanner-agent] No files found in {SCAN_DIR}")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
