"""Fetch Transfer Appliance status across GCP projects.

Uses the Transfer Appliance v1alpha1 REST API directly (no discovery doc),
with a fallback to gcloud CLI subprocess calls.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import google.auth
from google.auth.transport.requests import AuthorizedSession

TA_BASE_URL = "https://transferappliance.googleapis.com/v1alpha1"


@dataclass
class ProjectScanResult:
    project: str
    appliances: list[dict]
    error: str | None = None


@dataclass
class ScanResults:
    appliances: list[dict]
    errors: list[dict]


def _sanitize_display_name(value: object) -> str:
    text = str(value or "")
    sanitized = "".join(
        char if char >= " " and char != "\x7f" else " "
        for char in text
    )
    return " ".join(sanitized.split())


def _get_appliances_via_api(project_id: str) -> tuple[list[dict] | None, str | None]:
    """Fetch appliance orders from the v1alpha1 REST endpoint directly.

    Returns (appliances, None) on success, where appliances may be empty.
    Returns (None, error_message) on any transport, HTTP, or payload failure so
    callers can decide whether to fall back to gcloud.
    """
    credentials, quota_project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    headers = {}
    # User creds need X-Goog-User-Project to identify the billing project.
    if quota_project:
        headers["X-Goog-User-Project"] = quota_project

    url = f"{TA_BASE_URL}/projects/{project_id}/locations/-/appliances"
    try:
        response = session.get(url, headers=headers, timeout=30)
    except Exception as e:
        return None, f"[api] {project_id}: {type(e).__name__}: {e}"

    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError as e:
            return None, f"[api] {project_id}: invalid JSON: {e}"
        appliances = payload.get("appliances", [])
        if not isinstance(appliances, list):
            return None, (f"[api] {project_id}: invalid payload: "
                          "'appliances' must be a list")
        return appliances, None

    body = response.text[:200].replace("\n", " ")
    return None, f"[api] {project_id}: HTTP {response.status_code} {body}"


def _get_appliances_via_gcloud(project_id: str) -> tuple[list[dict] | None, str | None]:
    """Fallback: fetch appliances using gcloud alpha CLI."""
    try:
        result = subprocess.run(
            [
                "gcloud", "alpha", "transfer", "appliances", "orders",
                "list", "--project", project_id, "--format=json",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None, (f"[gcloud] {project_id}: rc={result.returncode} "
                          f"{result.stderr.strip()}")
        if not result.stdout.strip():
            return [], None

        payload = json.loads(result.stdout)
        if not isinstance(payload, list):
            return None, f"[gcloud] {project_id}: invalid payload: expected a list"
        return payload, None
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        return None, f"[gcloud] {project_id}: {type(e).__name__}: {e}"


def get_appliances_for_project(project_id: str) -> ProjectScanResult:
    """Get Transfer Appliance orders for a single project.

    Tries the v1alpha1 REST API first, then falls back to the gcloud CLI on
    any API failure. A project is marked failed only if both mechanisms fail.
    """
    appliances, api_error = _get_appliances_via_api(project_id)
    if api_error is not None:
        appliances, gcloud_error = _get_appliances_via_gcloud(project_id)
        if gcloud_error is not None:
            return ProjectScanResult(
                project=project_id,
                appliances=[],
                error=f"{api_error}; {gcloud_error}",
            )

    # Normalize each record
    normalized = []
    row_errors = []
    for a in appliances or []:
        full_name = a.get("name")
        if not isinstance(full_name, str) or not full_name:
            row_errors.append("record missing resource name")
            continue

        parsed_name = _parse_resource_name(full_name)
        if parsed_name is None:
            row_errors.append(f"invalid resource name: {full_name!r}")
            continue

        location, appliance_id = parsed_name
        normalized.append({
            "project": project_id,
            "name": full_name,
            "display_name": _sanitize_display_name(a.get("displayName", "")),
            "state": a.get("state", a.get("status", "UNKNOWN")),
            "model": a.get(
                "model",
                a.get("applianceModel", a.get("applianceType", a.get("type", "N/A"))),
            ),
            "create_time": a.get("createTime", "N/A"),
            "update_time": a.get("updateTime", "N/A"),
            "appliance_id": appliance_id,
            "location": location,
        })
    error = None
    if row_errors:
        sample = "; ".join(row_errors[:3])
        if len(row_errors) > 3:
            sample = f"{sample}; ..."
        error = (f"skipped {len(row_errors)} malformed appliance record(s): "
                 f"{sample}")
    return ProjectScanResult(project=project_id, appliances=normalized, error=error)


def _parse_resource_name(name: str) -> tuple[str, str] | None:
    """Pull (location, appliance_id) out of projects/x/locations/L/appliances/Z.

    Returns None if the string doesn't match the expected resource shape.
    """
    parts = name.split("/")
    # Expected shape: ["projects", P, "locations", L, "appliances", Z]
    if (
        len(parts) >= 6
        and parts[0] == "projects"
        and parts[2] == "locations"
        and parts[4] == "appliances"
    ):
        return parts[3], parts[-1]
    return None


def get_all_appliances(project_ids: list[str], max_workers: int = 10) -> ScanResults:
    """Fetch Transfer Appliance status across multiple projects in parallel.

    Args:
        project_ids: List of GCP project IDs to scan.
        max_workers: Max parallel threads for API calls.

    Returns:
        Aggregated appliances plus any project-level scan failures.
    """
    project_ids = list(dict.fromkeys(project_ids))
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
                result = future.result()
                all_appliances.extend(result.appliances)
                if result.error:
                    errors.append({"project": project_id, "error": result.error})
            except Exception as e:
                message = str(e)
                errors.append({"project": project_id, "error": message})
                print(f"Warning: failed to query project {project_id}: {e}",
                      file=sys.stderr)

    all_appliances.sort(key=lambda appliance: (
        appliance.get("project", ""),
        appliance.get("appliance_id", ""),
        appliance.get("name", ""),
    ))
    return ScanResults(appliances=all_appliances, errors=errors)
