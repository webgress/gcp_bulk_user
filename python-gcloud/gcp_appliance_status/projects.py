"""Discover GCP projects belonging to an organization."""

import sys
import time

from google.cloud import resourcemanager_v3


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def list_org_projects(org_id: str) -> list[dict]:
    """List all active projects under an organization.

    Args:
        org_id: GCP organization ID (numeric string).

    Returns:
        List of dicts with project_id, name, and state.
    """
    _log(f"[projects] cloudresourcemanager.googleapis.com searchProjects "
         f"org={org_id}")
    t0 = time.monotonic()
    client = resourcemanager_v3.ProjectsClient()
    request = resourcemanager_v3.SearchProjectsRequest(
        query=f"parent:organizations/{org_id} state:ACTIVE"
    )

    projects = []
    for project in client.search_projects(request=request):
        projects.append({
            "project_id": project.project_id,
            "name": project.display_name,
            "state": project.state.name,
        })
        if len(projects) % 50 == 0:
            _log(f"[projects] streamed {len(projects)} project(s) so far...")

    _log(f"[projects] searchProjects returned {len(projects)} project(s) in "
         f"{time.monotonic() - t0:.2f}s")
    return projects
