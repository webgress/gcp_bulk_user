"""Fetch Transfer Appliance status across GCP projects.

Uses the Transfer Appliance v1alpha1 REST API directly (no discovery doc),
with a fallback to gcloud CLI subprocess calls.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import google.auth
from google.auth.transport.requests import AuthorizedSession

TA_BASE_URL = "https://transferappliance.googleapis.com/v1alpha1"


def _get_appliances_via_api(project_id: str) -> list[dict] | None:
    """Fetch appliance orders from the v1alpha1 REST endpoint directly.

    Returns a list (possibly empty) on a successful API call, or None when the
    call itself failed — so callers can distinguish "API said zero orders"
    from "API call failed, try the fallback".
    """
    credentials, quota_project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    headers = {}
    # User creds need X-Goog-User-Project to identify the billing project.
    if quota_project:
        headers["X-Goog-User-Project"] = quota_project

    url = f"{TA_BASE_URL}/projects/{project_id}/locations/-/orders"
    try:
        response = session.get(url, headers=headers, timeout=30)
    except Exception as e:
        print(f"[api] {project_id}: {type(e).__name__}: {e}", file=sys.stderr)
        return None

    if response.status_code == 200:
        return response.json().get("orders", [])

    body = response.text[:200].replace("\n", " ")
    print(f"[api] {project_id}: HTTP {response.status_code} {body}", file=sys.stderr)
    return None


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
        if result.returncode != 0:
            print(f"[gcloud] {project_id}: rc={result.returncode} {result.stderr.strip()}",
                  file=sys.stderr)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[gcloud] {project_id}: {type(e).__name__}: {e}", file=sys.stderr)
    return []


def get_appliances_for_project(project_id: str) -> list[dict]:
    """Get Transfer Appliance orders for a single project.

    Tries the v1alpha1 REST API first. Only falls back to the gcloud CLI when
    the API call itself failed (returned None) — a successful empty response
    is treated as an authoritative "no appliances" and short-circuits.
    """
    appliances = _get_appliances_via_api(project_id)
    if appliances is None:
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
