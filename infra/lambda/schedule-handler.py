"""
Claude Code Platform -- Business Hours Scheduler
Triggered by EventBridge rules to manage node/pod lifecycle.

Actions:
  startup     -- Scale up nodes (weekday 09:00 KST)
  warning-30  -- Send 30-min shutdown warning (weekday 17:30 KST)
  warning-15  -- Send 15-min shutdown warning (weekday 17:45 KST)
  shutdown    -- Terminate pods + scale down nodes (weekday 18:00 KST)
  auto-scale  -- Remove empty nodes (every 30 min during business hours)
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
