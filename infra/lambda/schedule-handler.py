"""
Claude Code Platform -- Schedule Handler (DEPRECATED)

업무시간(09-18) 기반 강제 종료는 폐지됨 (2026-04-01).
EventBridge 5개 규칙 모두 DISABLED 상태.
Pod 수명은 사용자별 pod_ttl(7d/30d/unlimited)로만 관리.

이 Lambda는 더 이상 호출되지 않습니다.
향후 삭제 대상.
"""

import json
import os
import urllib.request
import urllib.error

API_BASE = os.environ.get("API_BASE_URL", "https://claude.skons.net")
ADMIN_TOKEN = os.environ.get("ADMIN_JWT_TOKEN", "")


def _call_api(path: str, method: str = "POST") -> dict:
    """Call Auth Gateway API with admin token."""
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"API error: {e.code} {body[:200]}")
        return {"error": e.code, "detail": body[:200]}
    except Exception as e:
        print(f"Request failed: {e}")
        return {"error": str(e)}


def handler(event, context):
    """Lambda handler -- dispatches based on event action."""
    action = event.get("action", "")
    print(f"Schedule action: {action}")

    if action == "startup":
        result = _call_api("/api/v1/schedule/startup?desired_nodes=2")
        print(f"Startup result: {result}")

    elif action == "warning-30":
        result = _call_api("/api/v1/schedule/shutdown-warning?minutes_before=30")
        print(f"Warning-30 result: {result}")

    elif action == "warning-15":
        result = _call_api("/api/v1/schedule/shutdown-warning?minutes_before=15")
        print(f"Warning-15 result: {result}")

    elif action == "shutdown":
        # 1. Shutdown pods (skip extended users)
        result = _call_api("/api/v1/schedule/shutdown")
        print(f"Shutdown result: {result}")
        # 2. Auto scale-down empty nodes
        scale_result = _call_api("/api/v1/admin/auto-scale-down")
        print(f"Scale-down result: {scale_result}")

    elif action == "auto-scale":
        result = _call_api("/api/v1/admin/auto-scale-down")
        print(f"Auto-scale result: {result}")

    else:
        print(f"Unknown action: {action}")
        return {"error": f"Unknown action: {action}"}

    return {"action": action, "status": "completed"}
