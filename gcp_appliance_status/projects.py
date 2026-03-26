"""Discover GCP projects belonging to an organization."""

from google.cloud import resourcemanager_v3


def list_org_projects(org_id: str) -> list[dict]:
    """List all active projects under an organization.

    Args:
        org_id: GCP organization ID (numeric string).

    Returns:
        List of dicts with project_id, name, and state.
    """
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

    return projects
