"""Diagnostic tool: inspect raw GCS bucket-list and Cloud Monitoring responses.

Use this to verify the shape of the data when results don't match expectations
(e.g. seeing far more pages than buckets you think exist).

Usage:
  python -m gcp_appliance_status.debug --project PROJECT_ID
  python -m gcp_appliance_status.debug --org-id ORG_ID --max-pages 5
  python -m gcp_appliance_status.debug --project P --max-results 1000 --page-size 100000

Output is to stdout — pipe to a file for sharing.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from datetime import datetime, timedelta, timezone

import google.auth
from google.auth.transport.requests import AuthorizedSession

from .projects import list_org_projects

GCS_URL = "https://storage.googleapis.com/storage/v1/b"
MON_URL = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
LOOKBACK_DAYS = 45


def _make_session() -> tuple[AuthorizedSession, dict]:
    credentials, quota_project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    headers: dict = {}
    if quota_project:
        headers["X-Goog-User-Project"] = quota_project
    return session, headers


def _short(token: str | None) -> str:
    if not token:
        return "-"
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def debug_buckets(
    project_id: str,
    session: AuthorizedSession,
    headers: dict,
    max_pages: int,
    max_results: int,
) -> None:
    print(f"\n=== buckets / {project_id} (maxResults={max_results}, "
          f"max_pages={max_pages or 'unlimited'}) ===", flush=True)
    print(f"GET {GCS_URL}?project={project_id}&maxResults={max_results}", flush=True)

    params: dict = {"project": project_id, "maxResults": str(max_results)}
    page = 0
    total_items = 0
    seen_names: set[str] = set()
    duplicates = 0
    seen_tokens: set[str] = set()
    repeated_tokens = 0

    while True:
        page += 1
        if max_pages and page > max_pages:
            print(f"  [STOP] reached --max-pages={max_pages}", flush=True)
            break

        t0 = time.monotonic()
        try:
            resp = session.get(GCS_URL, params=params, headers=headers, timeout=30)
        except Exception as e:
            print(f"  page {page}: REQUEST_ERROR {type(e).__name__}: {e}", flush=True)
            break
        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            body = resp.text[:300].replace("\n", " ")
            print(f"  page {page}: HTTP {resp.status_code} in {elapsed:.2f}s — {body}",
                  flush=True)
            break

        data = resp.json()

        if page == 1:
            top_keys = sorted(data.keys())
            print(f"  page 1 top-level keys: {top_keys}", flush=True)
            print(f"  page 1 'kind' value: {data.get('kind')!r}", flush=True)
            items_field = data.get("items")
            if isinstance(items_field, list) and items_field:
                first_item = items_field[0]
                if isinstance(first_item, dict):
                    print(f"  page 1 items[0] keys: {sorted(first_item.keys())}",
                          flush=True)
                    print(f"  page 1 items[0] 'kind': {first_item.get('kind')!r}",
                          flush=True)
                    print(f"  page 1 items[0] 'name': {first_item.get('name')!r}",
                          flush=True)
                else:
                    print(f"  WARNING: items[0] is not a dict — type={type(first_item).__name__}",
                          flush=True)
            elif items_field is None:
                print("  WARNING: response has no 'items' key at all", flush=True)
            else:
                print(f"  page 1 items field is empty list (type={type(items_field).__name__})",
                      flush=True)

        items = data.get("items", []) or []
        if not isinstance(items, list):
            print(f"  page {page}: items is not a list — type={type(items).__name__}",
                  flush=True)
            break

        page_dups = 0
        first_name = items[0].get("name") if items and isinstance(items[0], dict) else None
        last_name = items[-1].get("name") if items and isinstance(items[-1], dict) else None
        for it in items:
            if not isinstance(it, dict):
                continue
            n = it.get("name")
            if not isinstance(n, str):
                continue
            if n in seen_names:
                page_dups += 1
                duplicates += 1
            else:
                seen_names.add(n)
        total_items += len(items)

        next_token = data.get("nextPageToken")
        token_repeated = bool(next_token) and next_token in seen_tokens
        if token_repeated:
            repeated_tokens += 1
        if next_token:
            seen_tokens.add(next_token)

        print(
            f"  page {page:>4}: http=200 items={len(items):>4} "
            f"page_dups={page_dups:>3} unique_total={len(seen_names):>6} "
            f"token={_short(next_token)} token_repeated={token_repeated} "
            f"took={elapsed:.2f}s first={first_name!r} last={last_name!r}",
            flush=True,
        )

        if not next_token:
            break
        params = {**params, "pageToken": next_token}

    print(
        f"\n  summary: pages={page} items_seen={total_items} "
        f"unique_buckets={len(seen_names)} duplicate_names={duplicates} "
        f"repeated_tokens={repeated_tokens}",
        flush=True,
    )
    if duplicates > 0 or repeated_tokens > 0:
        print(
            "  >> SUSPECT: pagination is not advancing cleanly. The API may be "
            "returning duplicate items or recycling page tokens.",
            flush=True,
        )


def debug_monitoring(
    project_id: str,
    session: AuthorizedSession,
    headers: dict,
    max_pages: int,
    page_size: int | None,
) -> None:
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=LOOKBACK_DAYS)
    params: dict = {
        "filter": 'metric.type="storage.googleapis.com/storage/total_bytes"',
        "interval.startTime": start_time.isoformat(),
        "interval.endTime": end_time.isoformat(),
        "aggregation.alignmentPeriod": "86400s",
        "aggregation.perSeriesAligner": "ALIGN_MAX",
    }
    if page_size:
        params["pageSize"] = str(page_size)
    url = MON_URL.format(project=project_id)

    print(f"\n=== monitoring / {project_id} "
          f"(pageSize={page_size or 'API-default'}, "
          f"max_pages={max_pages or 'unlimited'}) ===", flush=True)
    print(f"GET {url}", flush=True)
    print(f"  filter: {params['filter']}", flush=True)
    print(f"  window: {start_time.isoformat()} -> {end_time.isoformat()}", flush=True)

    page = 0
    total_series = 0
    page_params = dict(params)
    seen_tokens: set[str] = set()
    repeated_tokens = 0

    while True:
        page += 1
        if max_pages and page > max_pages:
            print(f"  [STOP] reached --max-pages={max_pages}", flush=True)
            break

        t0 = time.monotonic()
        try:
            resp = session.get(url, params=page_params, headers=headers, timeout=60)
        except Exception as e:
            print(f"  page {page}: REQUEST_ERROR {type(e).__name__}: {e}", flush=True)
            break
        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            body = resp.text[:300].replace("\n", " ")
            print(f"  page {page}: HTTP {resp.status_code} in {elapsed:.2f}s — {body}",
                  flush=True)
            break

        data = resp.json()

        if page == 1:
            top_keys = sorted(data.keys())
            print(f"  page 1 top-level keys: {top_keys}", flush=True)
            series0 = (data.get("timeSeries") or [None])[0]
            if isinstance(series0, dict):
                print(f"  page 1 timeSeries[0] keys: {sorted(series0.keys())}",
                      flush=True)
                resource = series0.get("resource") or {}
                metric = series0.get("metric") or {}
                print(f"  page 1 timeSeries[0] resource.type={resource.get('type')!r} "
                      f"resource.labels={list((resource.get('labels') or {}).keys())}",
                      flush=True)
                print(f"  page 1 timeSeries[0] metric.type={metric.get('type')!r} "
                      f"metric.labels={list((metric.get('labels') or {}).keys())}",
                      flush=True)
                points = series0.get("points") or []
                print(f"  page 1 timeSeries[0] point_count={len(points)}", flush=True)

        series = data.get("timeSeries", []) or []
        total_series += len(series)
        next_token = data.get("nextPageToken")
        token_repeated = bool(next_token) and next_token in seen_tokens
        if token_repeated:
            repeated_tokens += 1
        if next_token:
            seen_tokens.add(next_token)

        print(
            f"  page {page:>4}: http=200 series={len(series):>5} "
            f"total_series={total_series:>6} token={_short(next_token)} "
            f"token_repeated={token_repeated} took={elapsed:.2f}s",
            flush=True,
        )

        if not next_token:
            break
        page_params = {**page_params, "pageToken": next_token}

    print(f"\n  summary: pages={page} total_series={total_series} "
          f"repeated_tokens={repeated_tokens}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Debug raw GCS/Monitoring API response shapes for a "
                    "project or org.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--project", help="Single GCP project ID to inspect.")
    target.add_argument("--org-id",
                        help="GCP org ID — discover and inspect every project.")
    parser.add_argument("--max-pages", type=int, default=20,
                        help="Safety cap per call (0 = unlimited). Default: 20.")
    parser.add_argument("--max-results", type=int, default=200,
                        help="GCS buckets.list maxResults per page (max 1000). "
                             "Default: 200 (matches production code).")
    parser.add_argument("--page-size", type=int, default=None,
                        help="Cloud Monitoring pageSize per request "
                             "(default: API default).")
    parser.add_argument("--no-buckets", action="store_true",
                        help="Skip the buckets.list inspection.")
    parser.add_argument("--no-monitoring", action="store_true",
                        help="Skip the timeSeries.list inspection.")
    args = parser.parse_args()

    if args.project:
        projects = [args.project]
    else:
        print(f"discovering projects in org {args.org_id}...", flush=True)
        projects = [p["project_id"] for p in list_org_projects(args.org_id)]
        print(f"found {len(projects)} project(s)", flush=True)

    session, headers = _make_session()
    max_pages = args.max_pages if args.max_pages > 0 else 0

    for pid in projects:
        if not args.no_buckets:
            debug_buckets(
                pid, session, headers,
                max_pages=max_pages, max_results=args.max_results,
            )
        if not args.no_monitoring:
            debug_monitoring(
                pid, session, headers,
                max_pages=max_pages, page_size=args.page_size,
            )


if __name__ == "__main__":
    main()
