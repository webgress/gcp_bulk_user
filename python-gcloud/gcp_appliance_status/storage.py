"""Fetch per-project GCS storage usage and history.

One Cloud Monitoring call per project. We use the storage/total_bytes metric
with crossSeriesReducer=REDUCE_SUM so Monitoring collapses every
(bucket x storage_class) series into a single project-wide daily series.
Response size is bounded (~45 daily points) regardless of bucket count.

Required IAM:
  - roles/monitoring.viewer  (monitoring.timeSeries.list)
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import google.auth
from google.auth.transport.requests import AuthorizedSession

_MON_URL = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
_LOOKBACK_DAYS = 45  # Cloud Monitoring retains hourly GCS samples ~6 weeks.


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


@dataclass
class ProjectStorageResult:
    project: str
    current_bytes: int = 0
    high_watermark_bytes: int = 0
    fill_date: Optional[str] = None   # ISO-8601; first observed 0 -> non-zero
    empty_date: Optional[str] = None  # ISO-8601; last observed non-zero -> 0
    error: Optional[str] = None


def _make_session() -> tuple[AuthorizedSession, dict]:
    credentials, quota_project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    headers: dict = {}
    if quota_project:
        headers["X-Goog-User-Project"] = quota_project
    return session, headers


def _get_project_storage_series(
    project_id: str, session: AuthorizedSession, headers: dict
) -> tuple[list[tuple[str, int]], Optional[str]]:
    """Return (sorted [(iso_timestamp, total_bytes)], error).

    Issues a single timeSeries.list call with cross-series sum aggregation
    so the server returns one series for the whole project.
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=_LOOKBACK_DAYS)
    params: dict = {
        "filter": 'metric.type="storage.googleapis.com/storage/total_bytes"',
        "interval.startTime": start_time.isoformat(),
        "interval.endTime": end_time.isoformat(),
        "aggregation.alignmentPeriod": "86400s",
        "aggregation.perSeriesAligner": "ALIGN_MAX",
        "aggregation.crossSeriesReducer": "REDUCE_SUM",
    }
    url = _MON_URL.format(project=project_id)

    _log(f"[storage] {project_id}: GET monitoring.googleapis.com (aggregated)")
    t0 = time.monotonic()
    try:
        resp = session.get(url, params=params, headers=headers, timeout=30)
    except Exception as e:
        _log(f"[storage] {project_id}: monitoring ERROR {type(e).__name__} after "
             f"{time.monotonic() - t0:.2f}s")
        return [], f"monitoring: {type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0

    if resp.status_code == 403:
        _log(f"[storage] {project_id}: monitoring HTTP 403 in {elapsed:.2f}s")
        return [], "monitoring: permission denied (HTTP 403)"
    if resp.status_code == 404:
        _log(f"[storage] {project_id}: monitoring HTTP 404 (not enabled) in "
             f"{elapsed:.2f}s")
        return [], None  # monitoring not enabled — treat as no data
    if resp.status_code != 200:
        _log(f"[storage] {project_id}: monitoring HTTP {resp.status_code} in "
             f"{elapsed:.2f}s")
        snippet = resp.text[:120].replace("\n", " ")
        return [], f"monitoring: HTTP {resp.status_code} {snippet}"

    try:
        data = resp.json()
    except ValueError as e:
        _log(f"[storage] {project_id}: monitoring HTTP 200 but invalid JSON in "
             f"{elapsed:.2f}s")
        return [], f"monitoring: invalid JSON: {e}"

    points: list[tuple[str, int]] = []
    for series in data.get("timeSeries") or []:
        for point in series.get("points") or []:
            ts = (point.get("interval") or {}).get("endTime", "")
            if not ts:
                continue
            val_obj = point.get("value") or {}
            raw = val_obj.get("int64Value")
            if raw is None:
                raw = val_obj.get("doubleValue", 0)
            try:
                val = int(float(str(raw)))
            except (ValueError, TypeError):
                val = 0
            points.append((ts, val))
    points.sort()

    _log(f"[storage] {project_id}: monitoring HTTP 200 "
         f"({len(points)} daily point(s)) in {elapsed:.2f}s")
    return points, None


def get_storage_for_project(project_id: str) -> ProjectStorageResult:
    try:
        session, headers = _make_session()
    except Exception as e:
        return ProjectStorageResult(
            project=project_id, error=f"auth: {type(e).__name__}: {e}"
        )

    points, err = _get_project_storage_series(project_id, session, headers)
    if not points:
        return ProjectStorageResult(project=project_id, error=err)

    high_watermark_bytes = max(v for _, v in points)
    current_bytes = points[-1][1]

    # fill_date: first observed transition from 0 -> non-zero in the window.
    # If data was already non-zero at window start, leave None — we don't
    # know when it actually rose.
    fill_date: Optional[str] = None
    prev_was_zero = False
    for ts, val in points:
        if val == 0:
            prev_was_zero = True
        elif prev_was_zero:
            fill_date = ts
            break

    # empty_date: most recent non-zero -> 0 transition, but only when
    # current_bytes is still 0 (otherwise the project refilled).
    empty_date: Optional[str] = None
    was_nonzero = False
    candidate_empty: Optional[str] = None
    for ts, val in points:
        if val > 0:
            was_nonzero = True
            candidate_empty = None
        elif was_nonzero:
            candidate_empty = ts
            was_nonzero = False
    if current_bytes == 0:
        empty_date = candidate_empty

    return ProjectStorageResult(
        project=project_id,
        current_bytes=current_bytes,
        high_watermark_bytes=high_watermark_bytes,
        fill_date=fill_date,
        empty_date=empty_date,
        error=err,
    )


def get_all_storage(
    project_ids: list[str], max_workers: int = 10
) -> dict[str, ProjectStorageResult]:
    """Fetch storage info for multiple projects in parallel."""
    total = len(project_ids)
    _log(f"[storage] starting storage queries for {total} project(s) "
         f"(max_workers={max_workers})")
    t_start = time.monotonic()
    results: dict[str, ProjectStorageResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_project = {
            executor.submit(get_storage_for_project, pid): pid
            for pid in project_ids
        }
        done = 0
        for future in as_completed(future_to_project):
            pid = future_to_project[future]
            try:
                results[pid] = future.result()
            except Exception as e:
                results[pid] = ProjectStorageResult(project=pid, error=str(e))
                _log(f"Warning: failed to query storage for {pid}: {e}")
            done += 1
            _log(f"[storage] progress {done}/{total} (last: {pid})")
    _log(f"[storage] all storage queries done in "
         f"{time.monotonic() - t_start:.2f}s")
    return results
