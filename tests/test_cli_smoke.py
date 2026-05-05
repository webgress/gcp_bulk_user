"""Offline CLI tests — no GCP calls, no credentials needed."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from gcp_appliance_status import cli
from gcp_appliance_status.appliances import (
    ProjectScanResult,
    ScanResults,
    _parse_resource_name,
    get_all_appliances,
    get_appliances_for_project,
)
from gcp_appliance_status.storage import (
    ProjectStorageResult,
    get_storage_for_project,
)


def run_cli(argv: list[str], scan_results: ScanResults) -> tuple[int, str, str]:
    """Run the CLI with stubbed GCP calls and capture output."""
    stdout = io.StringIO()
    stderr = io.StringIO()

    with patch(
        "gcp_appliance_status.cli.list_org_projects",
        return_value=[{"project_id": "p1", "name": "P1", "state": "ACTIVE"}],
    ), patch(
        "gcp_appliance_status.cli.get_all_appliances",
        return_value=scan_results,
    ), patch.object(
        sys,
        "argv",
        ["gcp_appliance_status"] + argv,
    ), redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            cli.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        else:
            code = 0

    return code, stdout.getvalue(), stderr.getvalue()


class CliSmokeTests(unittest.TestCase):
    def test_json_output_single_appliance(self) -> None:
        scan_results = ScanResults(
            appliances=[{
                "project": "p1",
                "name": "projects/p1/locations/us-central1/appliances/appliance-123",
                "state": "ACTIVE",
                "model": "TA40",
                "create_time": "2026-04-01T10:00:00Z",
                "update_time": "2026-04-10T12:00:00Z",
                "appliance_id": "appliance-123",
                "location": "us-central1",
            }],
            errors=[],
        )

        code, out, _ = run_cli(["--org-id", "999", "--format", "json"], scan_results)
        parsed = json.loads(out)
        appliances = parsed["appliances"]

        self.assertEqual(code, 0)
        self.assertIn("projects", parsed)
        self.assertEqual(appliances[0]["appliance_id"], "appliance-123")
        self.assertEqual(appliances[0]["state"], "ACTIVE")
        self.assertEqual(
            appliances[0]["project_url"],
            "https://pantheon.corp.google.com/appliances?project=p1",
        )
        self.assertEqual(
            appliances[0]["appliance_url"],
            "https://pantheon.corp.google.com/appliances/us-central1/"
            "appliance-123/details;tab=configuration?project=p1",
        )

    def test_state_filter_is_case_insensitive(self) -> None:
        scan_results = ScanResults(
            appliances=[{
                "project": "p1",
                "name": "n",
                "state": "active",
                "model": "TA40",
                "create_time": "t",
                "update_time": "t",
                "appliance_id": "a1",
                "location": "",
            }],
            errors=[],
        )

        code, out, _ = run_cli(
            ["--org-id", "999", "--format", "json", "--state-filter", "ACTIVE"],
            scan_results,
        )
        parsed = json.loads(out)
        appliances = parsed["appliances"]

        self.assertEqual(code, 0)
        self.assertEqual(len(appliances), 1)
        self.assertEqual(appliances[0]["appliance_id"], "a1")

    def test_csv_output_uses_appliance_id_contract(self) -> None:
        scan_results = ScanResults(
            appliances=[{
                "project": "p1",
                "name": "n",
                "state": "ACTIVE",
                "model": "TA40",
                "create_time": "t1",
                "update_time": "t2",
                "appliance_id": "a1",
                "location": "",
            }],
            errors=[],
        )

        code, out, _ = run_cli(["--org-id", "999", "--format", "csv"], scan_results)

        self.assertEqual(code, 0)
        self.assertIn(
            "project,project_url,appliance_id,appliance_url,model,state,create_time,update_time",
            out,
        )
        self.assertIn("https://pantheon.corp.google.com/appliances?project=p1", out)
        self.assertIn(
            "https://pantheon.corp.google.com/appliances?project=p1,"
            "a1,https://pantheon.corp.google.com/appliances?project=p1,TA40,ACTIVE,t1,t2",
            out,
        )

    def test_partial_scan_returns_results_and_nonzero_exit(self) -> None:
        scan_results = ScanResults(
            appliances=[{
                "project": "p1",
                "name": "n",
                "state": "ACTIVE",
                "model": "TA40",
                "create_time": "t1",
                "update_time": "t2",
                "appliance_id": "a1",
                "location": "",
            }],
            errors=[{"project": "p2", "error": "403 from API and gcloud"}],
        )

        code, out, err = run_cli(["--org-id", "999", "--format", "json"], scan_results)
        parsed = json.loads(out)
        appliances = parsed["appliances"]

        self.assertEqual(code, 2)
        self.assertEqual(len(appliances), 1)
        self.assertIn("results may be incomplete", err)
        self.assertIn("p2: 403 from API and gcloud", err)

    def test_failed_scan_with_no_successful_results_exits_nonzero(self) -> None:
        scan_results = ScanResults(
            appliances=[],
            errors=[{"project": "p1", "error": "403 from API and gcloud"}],
        )

        code, out, err = run_cli(["--org-id", "999", "--format", "json"], scan_results)

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("No Transfer Appliances found in successfully scanned projects.", err)

    def test_workers_must_be_positive(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(
            sys,
            "argv",
            ["gcp_appliance_status", "--org-id", "999", "--workers", "0"],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                cli.main()

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("must be greater than 0", stderr.getvalue())

    def test_discovery_failure_exits_cleanly(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch(
            "gcp_appliance_status.cli.list_org_projects",
            side_effect=RuntimeError("boom"),
        ), patch.object(
            sys,
            "argv",
            ["gcp_appliance_status", "--org-id", "999"],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                cli.main()

        self.assertEqual(exc.exception.code, 2)
        self.assertIn("Failed to discover projects: RuntimeError: boom", stderr.getvalue())

    def test_render_table_handles_markup_in_data(self) -> None:
        appliances = [{
            "project": "proj",
            "appliance_id": "id[/link][red]PWN[/red]",
            "model": "TA40",
            "state": "ACTIVE[/green][link=https://evil]",
            "create_time": "2026-04-01T10:00:00Z",
            "update_time": "2026-04-10T12:00:00Z",
            "location": "us-central1",
        }]

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            cli.render_table(appliances, cli.ZoneInfo("UTC"))

        rendered = stdout.getvalue()
        self.assertIn("id[/link]", rendered)
        self.assertIn("ACTIVE[/gre", rendered)

    def test_render_table_emits_pantheon_hyperlink(self) -> None:
        appliances = [{
            "project": "proj-123",
            "appliance_id": "appliance-xyz",
            "model": "TA40",
            "state": "ACTIVE",
            "create_time": "2026-04-01T10:00:00Z",
            "update_time": "2026-04-10T12:00:00Z",
            "location": "us-central1",
        }]

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            cli.render_table(appliances, cli.ZoneInfo("UTC"))

        rendered = stdout.getvalue()
        self.assertIn("\x1b]8;", rendered)
        self.assertIn(
            "https://pantheon.corp.google.com/appliances/us-central1/"
            "appliance-xyz/details;tab=configuration?project=proj-123",
            rendered,
        )

    def test_csv_formula_cells_are_prefixed(self) -> None:
        scan_results = ScanResults(
            appliances=[{
                "project": "p1",
                "name": "n",
                "state": "ACTIVE",
                "model": "TA40",
                "create_time": "2026-01-01",
                "update_time": "2026-01-02",
                "appliance_id": '=HYPERLINK("http://evil")',
                "location": "",
            }],
            errors=[],
        )

        code, out, _ = run_cli(["--org-id", "999", "--format", "csv"], scan_results)

        self.assertEqual(code, 0)
        self.assertIn('"\'=HYPERLINK(""http://evil"")"', out)

    def test_html_output_embeds_same_json_and_links(self) -> None:
        scan_results = ScanResults(
            appliances=[{
                "project": "p1",
                "name": "projects/p1/locations/us-central1/appliances/appliance-123",
                "state": "ACTIVE",
                "model": "TA40",
                "create_time": "2026-04-01T10:00:00Z",
                "update_time": "2026-04-10T12:00:00Z",
                "appliance_id": "appliance-123",
                "location": "us-central1",
            }],
            errors=[],
        )

        code, out, _ = run_cli(["--org-id", "999", "--format", "html"], scan_results)

        self.assertEqual(code, 0)
        self.assertIn('<script id="report-data" type="application/json">', out)
        self.assertIn('"project_url": "https://pantheon.corp.google.com/appliances?project=p1"', out)
        self.assertIn(
            '"appliance_url": "https://pantheon.corp.google.com/appliances/us-central1/'
            'appliance-123/details;tab=configuration?project=p1"',
            out,
        )
        self.assertIn('<button data-sort="model">Model</button>', out)

    def test_html_file_writes_report(self) -> None:
        scan_results = ScanResults(
            appliances=[{
                "project": "p1",
                "name": "projects/p1/locations/us-central1/appliances/appliance-123",
                "state": "ACTIVE",
                "model": "TA40",
                "create_time": "2026-04-01T10:00:00Z",
                "update_time": "2026-04-10T12:00:00Z",
                "appliance_id": "appliance-123",
                "location": "us-central1",
            }],
            errors=[],
        )

        with tempfile.TemporaryDirectory() as tempdir:
            report_path = Path(tempdir) / "report.html"
            code, out, err = run_cli(
                ["--org-id", "999", "--format", "html", "--html-file", str(report_path)],
                scan_results,
            )

            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertIn(f"Wrote HTML report to {report_path}", err)
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("Transfer Appliance Report — org 999", content)
            self.assertIn('"project_url": "https://pantheon.corp.google.com/appliances?project=p1"', content)

    def test_html_without_file_writes_tmp_and_opens_when_interactive(self) -> None:
        class TtyStringIO(io.StringIO):
            def isatty(self) -> bool:
                return True

        scan_results = ScanResults(
            appliances=[{
                "project": "p1",
                "name": "projects/p1/locations/us-central1/appliances/appliance-123",
                "state": "ACTIVE",
                "model": "TA40",
                "create_time": "2026-04-01T10:00:00Z",
                "update_time": "2026-04-10T12:00:00Z",
                "appliance_id": "appliance-123",
                "location": "us-central1",
            }],
            errors=[],
        )

        with tempfile.TemporaryDirectory() as tempdir:
            report_path = Path(tempdir) / "report.html"
            stdout = TtyStringIO()
            stderr = io.StringIO()
            with patch(
                "gcp_appliance_status.cli.list_org_projects",
                return_value=[{"project_id": "p1", "name": "P1", "state": "ACTIVE"}],
            ), patch(
                "gcp_appliance_status.cli.get_all_appliances",
                return_value=scan_results,
            ), patch(
                "gcp_appliance_status.cli._default_html_report_path",
                return_value=report_path,
            ), patch(
                "gcp_appliance_status.cli.subprocess.run",
                return_value=cli.subprocess.CompletedProcess(["open", str(report_path)], 0),
            ) as mocked_open, patch.object(
                sys,
                "argv",
                ["gcp_appliance_status", "--org-id", "999", "--format", "html"],
            ), patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
                cli.main()

            self.assertEqual(stdout.getvalue(), "")
            self.assertIn(f"Wrote HTML report to {report_path}", stderr.getvalue())
            mocked_open.assert_called_once_with(["open", str(report_path)], check=False)
            self.assertTrue(report_path.exists())


class ApplianceHelpersTests(unittest.TestCase):
    def test_appliance_url_matches_pantheon_format(self) -> None:
        self.assertEqual(
            cli._appliance_url("proj-123", "us-central1", "appliance-xyz"),
            ("https://pantheon.corp.google.com/appliances/us-central1/"
             "appliance-xyz/details;tab=configuration?project=proj-123"),
        )

    def test_project_url_matches_pantheon_format(self) -> None:
        self.assertEqual(
            cli._project_url("proj-123"),
            "https://pantheon.corp.google.com/appliances?project=proj-123",
        )

    def test_falls_back_to_gcloud_on_api_error(self) -> None:
        gcloud_payload = [{
            "name": "projects/p1/locations/us-central1/appliances/appliance-123",
            "state": "ACTIVE",
            "applianceType": "TA40",
            "createTime": "2026-04-01T10:00:00Z",
            "updateTime": "2026-04-10T12:00:00Z",
        }]

        with patch(
            "gcp_appliance_status.appliances._get_appliances_via_api",
            return_value=(None, "[api] p1: HTTP 403 denied"),
        ), patch(
            "gcp_appliance_status.appliances._get_appliances_via_gcloud",
            return_value=(gcloud_payload, None),
        ):
            result = get_appliances_for_project("p1")

        self.assertIsNone(result.error)
        self.assertEqual(len(result.appliances), 1)
        self.assertEqual(result.appliances[0]["appliance_id"], "appliance-123")
        self.assertEqual(result.appliances[0]["model"], "TA40")

    def test_order_resource_name_does_not_look_like_appliance(self) -> None:
        self.assertIsNone(
            _parse_resource_name("projects/p1/locations/us-central1/orders/order-123")
        )

    def test_subresource_resource_name_is_rejected(self) -> None:
        # If the API ever returns a child resource like an operation, we
        # must not silently treat the trailing segment as an appliance ID.
        self.assertIsNone(
            _parse_resource_name(
                "projects/p1/locations/L/appliances/A/operations/op1"
            )
        )

    def test_empty_segments_in_resource_name_are_rejected(self) -> None:
        for bad in [
            "projects/p1/locations/L/appliances/",
            "projects/p1/locations//appliances/A",
            "projects/p1/locations/L/appliances/A/",
            "projects//locations/L/appliances/A",
        ]:
            with self.subTest(name=bad):
                self.assertIsNone(_parse_resource_name(bad))

    def test_non_dict_records_are_skipped_not_crashing(self) -> None:
        # A malformed API payload that includes a non-dict entry must not
        # crash the whole per-project scan; it should be reported as a
        # skipped record and valid entries should still flow through.
        with patch(
            "gcp_appliance_status.appliances._get_appliances_via_api",
            return_value=(
                [
                    "not-a-dict",
                    {
                        "name": "projects/p1/locations/us-central1/appliances/a1",
                        "applianceType": "TA40",
                    },
                ],
                None,
            ),
        ):
            result = get_appliances_for_project("p1")

        self.assertEqual(len(result.appliances), 1)
        self.assertEqual(result.appliances[0]["appliance_id"], "a1")
        self.assertIn("non-object record", result.error or "")

    def test_null_state_is_coerced_to_string(self) -> None:
        # The API occasionally returns null fields; downstream code
        # (notably --state-filter) calls .upper(), which would crash on None.
        with patch(
            "gcp_appliance_status.appliances._get_appliances_via_api",
            return_value=(
                [{
                    "name": "projects/p1/locations/L/appliances/a1",
                    "state": None,
                    "applianceType": None,
                }],
                None,
            ),
        ):
            result = get_appliances_for_project("p1")

        self.assertEqual(result.appliances[0]["state"], "UNKNOWN")
        self.assertEqual(result.appliances[0]["model"], "N/A")
        # Must be safe to uppercase — this is what --state-filter does.
        result.appliances[0]["state"].upper()

    def test_malformed_resource_name_is_reported(self) -> None:
        with patch(
            "gcp_appliance_status.appliances._get_appliances_via_api",
            return_value=([{
                "name": "projects/p1/locations/us-central1/orders/order-123",
                "displayName": "bad\nname",
            }], None),
        ):
            result = get_appliances_for_project("p1")

        self.assertEqual(result.appliances, [])
        self.assertIn("skipped 1 malformed appliance record", result.error or "")

    def test_display_name_is_sanitized(self) -> None:
        with patch(
            "gcp_appliance_status.appliances._get_appliances_via_api",
            return_value=([{
                "name": "projects/p1/locations/us-central1/appliances/appliance-123",
                "displayName": "name\twith\ncontrols",
            }], None),
        ):
            result = get_appliances_for_project("p1")

        self.assertEqual(result.appliances[0]["display_name"], "name with controls")

    def test_duplicate_project_ids_are_deduped(self) -> None:
        fake = ProjectScanResult(
            project="p1",
            appliances=[{"project": "p1", "appliance_id": "a1", "name": "n"}],
        )

        with patch(
            "gcp_appliance_status.appliances.get_appliances_for_project",
            return_value=fake,
        ) as mocked:
            result = get_all_appliances(["p1", "p1"], max_workers=2)

        self.assertEqual(len(result.appliances), 1)
        self.assertEqual(mocked.call_count, 1)


def _appliance(project: str, state: str, appliance_id: str = "a1") -> dict:
    """Helper for building a normalized appliance dict in tests."""
    return {
        "project": project,
        "name": f"projects/{project}/locations/us-central1/appliances/{appliance_id}",
        "state": state,
        "model": "TA40",
        "create_time": "2026-01-01T00:00:00Z",
        "update_time": "2026-01-02T00:00:00Z",
        "appliance_id": appliance_id,
        "location": "us-central1",
    }


class BuildProjectSummariesTests(unittest.TestCase):
    """_build_project_summaries() decides active vs inactive per project."""

    def test_active_when_appliance_is_on_site(self) -> None:
        appliances = [_appliance("p1", "ON_SITE")]
        summaries = cli._build_project_summaries(appliances, {}, ["p1"])

        self.assertEqual(summaries["p1"]["status"], "active")
        self.assertEqual(summaries["p1"]["appliance_count"], 1)
        self.assertEqual(summaries["p1"]["appliance_states"], ["ON_SITE"])

    def test_active_when_wiped_but_storage_present(self) -> None:
        appliances = [_appliance("p1", "WIPED")]
        storage_results = {
            "p1": ProjectStorageResult(project="p1", current_bytes=1024),
        }
        summaries = cli._build_project_summaries(appliances, storage_results, ["p1"])

        self.assertEqual(summaries["p1"]["status"], "active")
        self.assertEqual(summaries["p1"]["appliance_count"], 1)
        self.assertEqual(summaries["p1"]["storage"]["current_bytes"], 1024)

    def test_inactive_when_wiped_with_no_storage(self) -> None:
        appliances = [_appliance("p1", "WIPED")]
        storage_results = {
            "p1": ProjectStorageResult(project="p1", current_bytes=0),
        }
        summaries = cli._build_project_summaries(appliances, storage_results, ["p1"])

        self.assertEqual(summaries["p1"]["status"], "inactive")
        self.assertEqual(summaries["p1"]["appliance_count"], 1)
        self.assertEqual(summaries["p1"]["storage"]["current_bytes"], 0)

    def test_inactive_when_no_appliances_and_no_storage(self) -> None:
        # No appliance entries, and either no storage record at all or one
        # whose current_bytes is 0 — both should yield "inactive".
        summaries = cli._build_project_summaries([], {}, ["p1"])

        self.assertEqual(summaries["p1"]["status"], "inactive")
        self.assertEqual(summaries["p1"]["appliance_count"], 0)
        self.assertEqual(summaries["p1"]["appliance_states"], [])
        self.assertIsNone(summaries["p1"]["storage"])


class BuildHtmlReportTests(unittest.TestCase):
    """build_html_report embeds the new {appliances, projects} payload."""

    def test_html_contains_projects_view_markers_and_dual_keys(self) -> None:
        appliances = [_appliance("p1", "ON_SITE")]
        project_summaries = {
            "p1": {
                "status": "active",
                "appliance_count": 1,
                "appliance_states": ["ON_SITE"],
                "storage": {
                    "current_bytes": 2048,
                    "high_watermark_bytes": 4096,
                    "fill_date": "2026-01-01T00:00:00Z",
                    "empty_date": None,
                    "bucket_count": 3,
                    "error": None,
                },
            },
        }

        html_doc = cli.build_html_report(
            appliances, project_summaries, "999", "America/Los_Angeles",
        )

        # Projects view markup is present.
        self.assertIn("view-projects", html_doc)
        self.assertIn("status-filter-btn", html_doc)
        self.assertIn("proj-rows", html_doc)

        # Embedded JSON has both top-level keys.
        marker = '<script id="report-data" type="application/json">'
        start = html_doc.index(marker) + len(marker)
        end = html_doc.index("</script>", start)
        embedded = json.loads(html_doc[start:end])
        self.assertIn("appliances", embedded)
        self.assertIn("projects", embedded)
        self.assertEqual(embedded["appliances"][0]["appliance_id"], "a1")
        self.assertEqual(embedded["projects"]["p1"]["status"], "active")


class ProjectStorageResultTests(unittest.TestCase):
    """Smoke tests for the storage.ProjectStorageResult dataclass."""

    def test_minimum_construction_uses_zero_defaults(self) -> None:
        result = ProjectStorageResult(project="p1")

        self.assertEqual(result.project, "p1")
        self.assertEqual(result.bucket_count, 0)
        self.assertEqual(result.current_bytes, 0)
        self.assertEqual(result.high_watermark_bytes, 0)
        self.assertIsNone(result.fill_date)
        self.assertIsNone(result.empty_date)
        self.assertIsNone(result.error)

    def test_full_construction_keeps_all_fields(self) -> None:
        result = ProjectStorageResult(
            project="p1",
            bucket_count=2,
            current_bytes=100,
            high_watermark_bytes=500,
            fill_date="2026-01-01T00:00:00Z",
            empty_date="2026-04-01T00:00:00Z",
            error=None,
        )

        self.assertEqual(result.bucket_count, 2)
        self.assertEqual(result.current_bytes, 100)
        self.assertEqual(result.high_watermark_bytes, 500)
        self.assertEqual(result.fill_date, "2026-01-01T00:00:00Z")
        self.assertEqual(result.empty_date, "2026-04-01T00:00:00Z")


class NoStorageFlagTests(unittest.TestCase):
    """--no-storage must skip get_all_storage entirely."""

    def test_no_storage_flag_skips_get_all_storage(self) -> None:
        scan_results = ScanResults(
            appliances=[_appliance("p1", "ON_SITE")],
            errors=[],
        )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "gcp_appliance_status.cli.list_org_projects",
            return_value=[{"project_id": "p1", "name": "P1", "state": "ACTIVE"}],
        ), patch(
            "gcp_appliance_status.cli.get_all_appliances",
            return_value=scan_results,
        ), patch(
            "gcp_appliance_status.cli.get_all_storage",
        ) as mocked_storage, patch.object(
            sys,
            "argv",
            ["gcp_appliance_status", "--org-id", "999", "--format", "json",
             "--no-storage"],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                cli.main()
            except SystemExit:
                pass

        # The whole point of --no-storage: skip the storage query entirely.
        self.assertEqual(mocked_storage.call_count, 0)

        # And the JSON payload still has both keys, with a storage=None summary.
        parsed = json.loads(stdout.getvalue())
        self.assertEqual(parsed["projects"]["p1"]["storage"], None)


class FakeResponse:
    """Minimal stand-in for a requests.Response from AuthorizedSession.get."""

    def __init__(self, status_code: int, payload: dict | None = None,
                 text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def _route_session_get(routes: dict[str, list]):
    """Return a side_effect that picks a response based on which URL is hit.

    routes maps a substring (e.g. "storage.googleapis.com") to a list of
    FakeResponse objects, consumed in order on each matching call.
    """

    def _side_effect(url, *args, **kwargs):
        for needle, responses in routes.items():
            if needle in url:
                if not responses:
                    raise AssertionError(
                        f"No more fake responses queued for URL containing {needle!r}"
                    )
                return responses.pop(0)
        raise AssertionError(f"Unexpected URL in test: {url}")

    return _side_effect


class StorageApiTests(unittest.TestCase):
    """Mock AuthorizedSession.get to verify storage.py behaviour."""

    def _patch_session(self):
        """Patch _make_session to return (session_mock, headers_dict)."""

        class _SessionMock:
            def __init__(self) -> None:
                self.get = None  # populated per test

        session_mock = _SessionMock()
        return session_mock, patch(
            "gcp_appliance_status.storage._make_session",
            return_value=(session_mock, {}),
        )

    def test_fill_date_picks_oldest_bucket_time_created(self) -> None:
        session_mock, session_patch = self._patch_session()

        # Two buckets — the older timeCreated should be picked as fill_date.
        bucket_resp = FakeResponse(200, {
            "items": [
                {"name": "bucket-new", "timeCreated": "2026-03-01T00:00:00Z"},
                {"name": "bucket-old", "timeCreated": "2025-12-01T00:00:00Z"},
            ],
        })
        # Empty time series — focus this test on fill_date only.
        ts_resp = FakeResponse(200, {"timeSeries": []})

        session_mock.get = MagicMock(  # type: ignore[attr-defined]
            side_effect=_route_session_get({
                "storage.googleapis.com": [bucket_resp],
                "monitoring.googleapis.com": [ts_resp],
            }),
        )

        with session_patch:
            result = get_storage_for_project("p1")

        self.assertEqual(result.fill_date, "2025-12-01T00:00:00Z")
        self.assertEqual(result.bucket_count, 2)
        self.assertIsNone(result.error)

    def test_high_watermark_sums_across_buckets_and_classes(self) -> None:
        session_mock, session_patch = self._patch_session()

        bucket_resp = FakeResponse(200, {
            "items": [{"name": "b1", "timeCreated": "2026-01-01T00:00:00Z"}],
        })
        # Two time series (e.g. bucket × storage class), two timestamps each.
        # At t1: 100 + 50 = 150 ; at t2: 200 + 300 = 500.
        # high_watermark_bytes should be 500, current_bytes (last) also 500.
        ts_resp = FakeResponse(200, {
            "timeSeries": [
                {
                    "points": [
                        {"interval": {"endTime": "2026-04-01T00:00:00Z"},
                         "value": {"int64Value": "100"}},
                        {"interval": {"endTime": "2026-04-02T00:00:00Z"},
                         "value": {"int64Value": "200"}},
                    ],
                },
                {
                    "points": [
                        {"interval": {"endTime": "2026-04-01T00:00:00Z"},
                         "value": {"int64Value": "50"}},
                        {"interval": {"endTime": "2026-04-02T00:00:00Z"},
                         "value": {"int64Value": "300"}},
                    ],
                },
            ],
        })

        session_mock.get = MagicMock(  # type: ignore[attr-defined]
            side_effect=_route_session_get({
                "storage.googleapis.com": [bucket_resp],
                "monitoring.googleapis.com": [ts_resp],
            }),
        )

        with session_patch:
            result = get_storage_for_project("p1")

        self.assertEqual(result.high_watermark_bytes, 500)
        self.assertEqual(result.current_bytes, 500)
        # Storage never dropped to zero, so empty_date remains None.
        self.assertIsNone(result.empty_date)

    def test_empty_date_set_when_storage_drops_to_zero(self) -> None:
        session_mock, session_patch = self._patch_session()

        bucket_resp = FakeResponse(200, {
            "items": [{"name": "b1", "timeCreated": "2026-01-01T00:00:00Z"}],
        })
        # Storage was 1000, then 500, then 0 — empty_date should be the
        # timestamp of the first zero point and current_bytes should be 0.
        ts_resp = FakeResponse(200, {
            "timeSeries": [
                {
                    "points": [
                        {"interval": {"endTime": "2026-04-01T00:00:00Z"},
                         "value": {"int64Value": "1000"}},
                        {"interval": {"endTime": "2026-04-02T00:00:00Z"},
                         "value": {"int64Value": "500"}},
                        {"interval": {"endTime": "2026-04-03T00:00:00Z"},
                         "value": {"int64Value": "0"}},
                    ],
                },
            ],
        })

        session_mock.get = MagicMock(  # type: ignore[attr-defined]
            side_effect=_route_session_get({
                "storage.googleapis.com": [bucket_resp],
                "monitoring.googleapis.com": [ts_resp],
            }),
        )

        with session_patch:
            result = get_storage_for_project("p1")

        self.assertEqual(result.current_bytes, 0)
        self.assertEqual(result.high_watermark_bytes, 1000)
        self.assertEqual(result.empty_date, "2026-04-03T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
