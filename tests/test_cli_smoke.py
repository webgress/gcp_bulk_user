"""Offline CLI tests — no GCP calls, no credentials needed."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from gcp_appliance_status import cli
from gcp_appliance_status.appliances import (
    ProjectScanResult,
    ScanResults,
    _parse_resource_name,
    get_all_appliances,
    get_appliances_for_project,
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

        self.assertEqual(code, 0)
        self.assertEqual(parsed[0]["appliance_id"], "appliance-123")
        self.assertEqual(parsed[0]["state"], "ACTIVE")

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

        self.assertEqual(code, 0)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["appliance_id"], "a1")

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
        self.assertIn("project,appliance_id,model,state,create_time,update_time", out)
        self.assertIn("p1,a1,TA40,ACTIVE,t1,t2", out)

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

        self.assertEqual(code, 2)
        self.assertEqual(len(parsed), 1)
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
            "appliance-xyz;tab=configuration?project=proj-123",
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


class ApplianceHelpersTests(unittest.TestCase):
    def test_appliance_url_matches_pantheon_format(self) -> None:
        self.assertEqual(
            cli._appliance_url("proj-123", "us-central1", "appliance-xyz"),
            ("https://pantheon.corp.google.com/appliances/us-central1/"
             "appliance-xyz;tab=configuration?project=proj-123"),
        )

    def test_project_url_matches_pantheon_format(self) -> None:
        self.assertEqual(
            cli._project_url("proj-123"),
            "https://pantheon.corp.google.com/home/dashboard?project=proj-123",
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


if __name__ == "__main__":
    unittest.main()
