"""Fetch GCS storage usage and history for GCP projects.

Queries:
  - GCS JSON API to list buckets (bucket creation time used as fill-date proxy)
  - Cloud Monitoring API for storage/total_bytes time series (high watermark,
    current bytes, and empty-date detection within the retention window)

Required IAM roles (in addition to existing appliance roles):
  - roles/storage.objectViewer  (or broader)  — storage.buckets.list
  - roles/monitoring.viewer                   — monitoring.timeSeries.list
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

_GCS_URL = "https://storage.googleapis.com/storage/v1/b"
_MON_URL = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
_LOOKBACK_DAYS = 45  # Cloud Monitoring retains ~6 weeks of hourly GCS metrics


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


@dataclass
class ProjectStorageResult:
    project: str
    bucket_count: int = 0
    current_bytes: int = 0
    high_watermark_bytes: int = 0
    fill_date: Optional[str] = None   # ISO-8601; oldest bucket timeCreated
    empty_date: Optional[str] = None  # ISO-8601; last point storage hit 0
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


def _list_buckets(
    project_id: str, session: AuthorizedSession, headers: dict
) -> tuple[list[dict], Optional[str]]:
    """Return (buckets, error).  buckets is a list of GCS bucket metadata dicts."""
    params: dict = {
        "project": project_id,
        "fields": "nextPageToken,items(name,timeCreated)",
        "maxResults": "200",
    }
    buckets: list[dict] = []
    page = 0
    while True:
        page += 1
        _log(f"[storage] {project_id}: GET storage.googleapis.com buckets page {page}")
        t0 = time.monotonic()
        try:
            resp = session.get(_GCS_URL, params=params, headers=headers, timeout=30)
        except Exception as e:
            _log(f"[storage] {project_id}: buckets ERROR {type(e).__name__} after "
                 f"{time.monotonic() - t0:.2f}s")
            return buckets, f"buckets: {type(e).__name__}: {e}"
        elapsed = time.monotonic() - t0
        if resp.status_code == 403:
            _log(f"[storage] {project_id}: buckets HTTP 403 in {elapsed:.2f}s")
            return [], "buckets: permission denied (HTTP 403)"
        if resp.status_code == 404:
            _log(f"[storage] {project_id}: buckets HTTP 404 (GCS not enabled) in "
                 f"{elapsed:.2f}s")
            return [], None  # GCS API not enabled; not an error
        if resp.status_code != 200:
            _log(f"[storage] {project_id}: buckets HTTP {resp.status_code} in "
                 f"{elapsed:.2f}s")
            snippet = resp.text[:120].replace("\n", " ")
            return buckets, f"buckets: HTTP {resp.status_code} {snippet}"
        try:
            data = resp.json()
        except ValueError as e:
            _log(f"[storage] {project_id}: buckets HTTP 200 but invalid JSON in "
                 f"{elapsed:.2f}s")
            return buckets, f"buckets: invalid JSON: {e}"
        items = data.get("items", [])
        buckets.extend(items)
        _log(f"[storage] {project_id}: buckets page {page} returned {len(items)} "
             f"in {elapsed:.2f}s (total {len(buckets)})")
        next_page = data.get("nextPageToken")
        if not next_page:
            break
        params = {**params, "pageToken": next_page}
    return buckets, None


def _get_storage_timeseries(
    project_id: str, session: AuthorizedSession, headers: dict
) -> tuple[list[tuple[str, int]], Optional[str]]:
    """Return sorted list of (iso_timestamp, total_bytes_across_all_buckets)."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=_LOOKBACK_DAYS)

    params: dict = {
        "filter": 'metric.type="storage.googleapis.com/storage/total_bytes"',
        "interval.startTime": start_time.isoformat(),
        "interval.endTime": end_time.isoformat(),
        "aggregation.alignmentPeriod": "86400s",
        "aggregation.perSeriesAligner": "ALIGN_MAX",
    }
    url = _MON_URL.format(project=project_id)

    all_series: list[dict] = []
    page_params = dict(params)
    page = 0
    while True:
        page += 1
        _log(f"[storage] {project_id}: GET monitoring.googleapis.com timeSeries "
             f"page {page}")
        t0 = time.monotonic()
        try:
            resp = session.get(url, params=page_params, headers=headers, timeout=30)
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
            return [], None  # monitoring not enabled
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
        series = data.get("timeSeries", [])
        all_series.extend(series)
        _log(f"[storage] {project_id}: monitoring page {page} returned {len(series)} "
             f"series in {elapsed:.2f}s (total {len(all_series)})")
        next_page = data.get("nextPageToken")
        if not next_page:
            break
        page_params = {**page_params, "pageToken": next_page}

    # Sum across all bucket/storage-class time series per timestamp.
    totals: dict[str, int] = {}
    for series in all_series:
        for point in series.get("points", []):
            ts = point.get("interval", {}).get("endTime", "")
            if not ts:
                continue
            val_obj = point.get("value", {})
            raw = val_obj.get("int64Value")
            if raw is None:
                raw = val_obj.get("doubleValue", 0)
            try:
                val = int(float(str(raw)))
            except (ValueError, TypeError):
                val = 0
            totals[ts] = totals.get(ts, 0) + val

    return sorted(totals.items()), None


def get_storage_for_project(project_id: str) -> ProjectStorageResult:
    try:
        session, headers = _make_session()
    except Exception as e:
        return ProjectStorageResult(
            project=project_id, error=f"auth: {type(e).__name__}: {e}"
        )

    buckets, bucket_err = _list_buckets(project_id, session, headers)
    ts_data, ts_err = _get_storage_timeseries(project_id, session, headers)

    # fill_date: creation time of the oldest bucket in the project.
    fill_date: Optional[str] = None
    times = [b["timeCreated"] for b in buckets if b.get("timeCreated")]
    if times:
        fill_date = min(times)

    current_bytes = 0
    high_watermark_bytes = 0
    empty_date: Optional[str] = None

    if ts_data:
        high_watermark_bytes = max(v for _, v in ts_data)
        current_bytes = ts_data[-1][1]

        # Detect the last timestamp where storage transitioned from non-zero to 0.
        was_nonzero = False
        _candidate_empty: Optional[str] = None
        for ts, val in ts_data:
            if val > 0:
                was_nonzero = True
                _candidate_empty = None  # reset: still active past this point
            elif was_nonzero:
                _candidate_empty = ts
                was_nonzero = False
        if current_bytes == 0:
            empty_date = _candidate_empty

    errors = [e for e in [bucket_err, ts_err] if e]
    return ProjectStorageResult(
        project=project_id,
        bucket_count=len(buckets),
        current_bytes=current_bytes,
        high_watermark_bytes=high_watermark_bytes,
        fill_date=fill_date,
        empty_date=empty_date,
        error="; ".join(errors) if errors else None,
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
