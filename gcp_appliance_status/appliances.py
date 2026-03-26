"""Fetch Transfer Appliance status across GCP projects.

Uses the Transfer Appliance API via google-api-python-client discovery,
with a fallback to gcloud CLI subprocess calls.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def _get_appliances_via_api(project_id: str) -> list[dict]:
    """Try fetching appliances using the discovery API."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    try:
        service = build(
            "transferappliance", "v1",
            credentials=credentials,
            static_discovery=False,
        )
        # List appliance orders for the project
        request = service.projects().locations().orders().list(
            parent=f"projects/{project_id}/locations/-"
        )
        response = request.execute()
        return response.get("orders", [])
    except HttpError as e:
        if e.resp.status in (403, 404):
            return []
        raise
    except Exception:
        return []


def _get_appliances_via_gcloud(project_id: str) -> list[dict]:
    """Fallback: fetch appliances using gcloud alpha CLI."""
    try:
        result = subprocess.run(
            [
                "gcloud", "alpha", "transfer", "appliances", "orders",
                "list", "--project", project_id, "--format=json",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def get_appliances_for_project(project_id: str) -> list[dict]:
    """Get Transfer Appliance orders for a single project.

    Tries the discovery API first, falls back to gcloud CLI.
    """
    appliances = _get_appliances_via_api(project_id)
    if not appliances:
        appliances = _get_appliances_via_gcloud(project_id)

    # Normalize each record
    normalized = []
    for a in appliances:
        normalized.append({
            "project": project_id,
            "name": a.get("name", a.get("displayName", "unknown")),
            "state": a.get("state", a.get("status", "UNKNOWN")),
            "type": a.get("applianceType", a.get("type", "N/A")),
            "create_time": a.get("createTime", "N/A"),
            "update_time": a.get("updateTime", "N/A"),
            "order_id": _extract_order_id(a.get("name", "")),
        })
    return normalized


def _extract_order_id(name: str) -> str:
    """Extract order ID from resource name like projects/x/locations/y/orders/z."""
    parts = name.split("/")
    return parts[-1] if parts else name


def get_all_appliances(project_ids: list[str], max_workers: int = 10) -> list[dict]:
    """Fetch Transfer Appliance status across multiple projects in parallel.

    Args:
        project_ids: List of GCP project IDs to scan.
        max_workers: Max parallel threads for API calls.

    Returns:
        Aggregated list of appliance records across all projects.
    """
    all_appliances = []
    errors = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_project = {
            executor.submit(get_appliances_for_project, pid): pid
            for pid in project_ids
        }
        for future in as_completed(future_to_project):
            project_id = future_to_project[future]
            try:
                results = future.result()
                all_appliances.extend(results)
            except Exception as e:
                errors.append({"project": project_id, "error": str(e)})
                print(f"Warning: failed to query project {project_id}: {e}",
                      file=sys.stderr)

    return all_appliances
