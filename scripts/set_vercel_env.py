#!/usr/bin/env python3
"""Set Vercel project environment variables via the REST API."""
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.environ["VERCEL_TOKEN"]
TEAM_ID = os.environ.get("VERCEL_TEAM_ID", "team_FUCk3Rij4hBd56ZSXOxDbRjW")


def api_request(method, path, body=None):
    url = f"https://api.vercel.com{path}"
    if TEAM_ID:
        url += ("&" if "?" in path else "?") + f"teamId={TEAM_ID}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        print(f"ERROR {method} {path}: {exc.code} {body}", file=sys.stderr)
        raise


def set_envs(project_id, envs, target="production"):
    """Replace env vars for the given project and target."""
    existing = api_request("GET", f"/v9/projects/{project_id}/env")
    ids_to_delete = []
    for entry in existing.get("envs", []):
        if entry["key"] in envs and target in entry.get("target", []):
            ids_to_delete.append(entry["id"])
    for env_id in ids_to_delete:
        api_request("DELETE", f"/v9/projects/{project_id}/env/{env_id}")
        print(f"  deleted old {env_id}")
    for key, value in envs.items():
        api_request(
            "POST",
            f"/v10/projects/{project_id}/env",
            {
                "key": key,
                "value": value,
                "type": "plain",
                "target": [target],
            },
        )
        print(f"  set {key}")


CONTROL_PLANE = {
    "MIDDLEWARE_BASE_URL": "https://dss-system-middleware.fly.dev",
    "BACKEND_BASE_URL": "https://dss-system-backend.fly.dev",
    "AUTH_BASE_URL": "https://id.dualsubstrate.com",
    "CHAT_BASE_URL": "https://chat.dualsubstrate.com",
    "PUBLIC_BASE_URL": "https://id.dualsubstrate.com",
    "DEFAULT_DID_HOST": "id.dualsubstrate.com",
    "ISSUER_DID": "did:web:id.dualsubstrate.com",
    "WALT_ID_BASE_URL": "https://dss-system-did-issuer.fly.dev",
    "WALT_ID_ISSUER_DID": "did:web:id.dualsubstrate.com",
    "TRUST_ANCHOR_PUBLIC_BASE_URL": "https://id.dualsubstrate.com",
}

CHAT_SURFACE = {
    "API_BASE": "https://dss-system-middleware.fly.dev",
    "DUALSUBSTRATE_API": "https://dss-system-middleware.fly.dev",
    "MIDDLEWARE_URL": "https://dss-system-middleware.fly.dev",
    "MIDDLEWARE_BASE_URL": "https://dss-system-middleware.fly.dev",
    "VITE_MIDDLEWARE_BASE_URL": "https://dss-system-middleware.fly.dev",
    "CONTROL_PLANE_BASE": "https://id.dualsubstrate.com",
    "DUALSUBSTRATE_CONTROL_PLANE_BASE": "https://id.dualsubstrate.com",
    "BACKEND_ADMIN_BASE": "https://dss-system-backend.fly.dev",
    "DUALSUBSTRATE_AUTH_BASE": "https://id.dualsubstrate.com",
    "DEFAULT_LEDGER_ID": "LOAM",
    "VITE_DEFAULT_LEDGER": "LOAM",
}

COORD_DEMO = {
    "MIDDLEWARE_BASE_URL": "https://dss-system-middleware.fly.dev",
}

if __name__ == "__main__":
    print("Updating dss-dashboard (control-plane)...")
    set_envs("prj_hcY4AduxMTsi6c0U3zic3ZotpObf", CONTROL_PLANE)
    print("Updating ds-frontend-local-new (chat-surface)...")
    set_envs("prj_Ek3eqLAAVq3UHeM2DkN8CyKE677I", CHAT_SURFACE)
    print("Updating coord-demo...")
    set_envs("prj_meE7m9OmaHUITRc4uphTv3fcIVj7", COORD_DEMO)
    print("Done.")
