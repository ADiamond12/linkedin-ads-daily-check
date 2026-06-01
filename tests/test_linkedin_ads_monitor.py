import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

import linkedin_ads_monitor as lam


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "sample_linkedin_ads.csv"


class LinkedinAdsMonitorTests(unittest.TestCase):
    def test_split_campaign_name_supports_both_shapes(self) -> None:
        seven = lam.split_campaign_name(
            "Q1'26 | prod_05 | AMS | GDQA + InICP + FFTA | Evaluation | Site Visits | Image"
        )
        eight = lam.split_campaign_name(
            "Q1'26 | prod_17aa | AMS + EMEA | Not Consented | GDQA + Procurement team | Tofu Conversion | Lead Gen | Image"
        )
        self.assertEqual(seven["consent_status"], "")
        self.assertEqual(seven["audience"], "GDQA + InICP + FFTA")
        self.assertEqual(seven["objective"], "Site Visits")
        self.assertEqual(eight["consent_status"], "Not Consented")
        self.assertEqual(eight["audience"], "GDQA + Procurement team")
        self.assertEqual(eight["objective"], "Lead Gen")

    def test_aggregate_calculates_metrics(self) -> None:
        rows = lam.normalize_rows(
            [
                {
                    "Date": "2026-03-03",
                    "Campaign name": "Q1'26 | prod | WW | Procurement | Tofu Conversion | Lead Gen | Image",
                    "Impressions": "1000",
                    "Clicks": "20",
                    "Total spent": "50",
                    "Conversions": "2",
                    "Leads": "1",
                },
                {
                    "Date": "2026-03-03",
                    "Campaign name": "Q1'26 | prod | WW | Not Consented | Procurement | Mid | Site Visits | Image",
                    "Impressions": "3000",
                    "Clicks": "30",
                    "Total spent": "90",
                    "Conversions": "3",
                    "Leads": "0",
                },
            ]
        )
        summary = lam.aggregate(rows)
        self.assertEqual(summary["impressions"], 4000)
        self.assertEqual(summary["clicks"], 50)
        self.assertAlmostEqual(summary["cpc"], 2.8)
        self.assertAlmostEqual(summary["ctr"], 1.25)
        self.assertAlmostEqual(summary["cpl"], 140.0)
        self.assertAlmostEqual(summary["conversion_rate"], 10.0)

    def test_build_pacing_classifies_statuses(self) -> None:
        latest = date(2026, 3, 10)
        self.assertEqual(lam.build_pacing(1000, 200, latest)["label"], "Under pace")
        self.assertEqual(lam.build_pacing(1000, 323, latest)["label"], "On pace")
        self.assertEqual(lam.build_pacing(1000, 500, latest)["label"], "Over pace")

    def test_campaign_alerts_rank_highest_severity_first(self) -> None:
        rows = lam.normalize_rows(lam.load_rows("", str(FIXTURE_PATH)))
        latest_rows = [row for row in rows if row["date"] == date(2026, 3, 3)]
        alerts = lam.build_campaign_alerts(latest_rows, lam.DEFAULT_TARGETS, limit=5)
        self.assertGreaterEqual(len(alerts), 2)
        self.assertIn("prod_alpha", alerts[0]["campaign_name"])
        self.assertTrue(any("Lead gen spend without leads" in reason for reason in alerts[0]["reasons"]))

    def test_zero_division_metrics_return_none(self) -> None:
        metrics = lam.campaign_metrics(
            {
                "spend": 100.0,
                "clicks": 0,
                "impressions": 0,
                "leads": 0,
                "conversions": 0,
            }
        )
        self.assertIsNone(metrics["cpc"])
        self.assertIsNone(metrics["ctr"])
        self.assertIsNone(metrics["cpl"])
        self.assertIsNone(metrics["conversion_rate"])

    def test_missing_ai_key_falls_back_cleanly(self) -> None:
        config = lam.AIProviderConfig(
            enabled=True,
            provider="openai",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            api_key_env="DEF_NOT_SET_FOR_TEST",
        )
        old_value = os.environ.pop("DEF_NOT_SET_FOR_TEST", None)
        try:
            result = lam.generate_analyst_note({"latest_date": "2026-03-03"}, config, ["Fallback line"])
        finally:
            if old_value is not None:
                os.environ["DEF_NOT_SET_FOR_TEST"] = old_value
        self.assertEqual(result.lines, ["Fallback line"])
        self.assertEqual(result.source_label, "Rule-based fallback")
        self.assertIn("DEF_NOT_SET_FOR_TEST", result.detail)

    def test_cli_generates_artifacts_from_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = lam.main(
                    [
                        "--csv-path",
                        str(FIXTURE_PATH),
                        "--monthly-budget",
                        "1000",
                        "--output-dir",
                        tmpdir,
                    ]
                )
            self.assertEqual(exit_code, 0, stderr.getvalue())

            output_dir = Path(tmpdir)
            self.assertTrue((output_dir / "latest_report.html").exists())
            self.assertTrue((output_dir / "latest_report.json").exists())
            self.assertTrue((output_dir / "latest_summary.md").exists())

            report = json.loads((output_dir / "latest_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["latest_date"], "2026-03-03")
            self.assertEqual(report["effective_source"], FIXTURE_PATH.as_posix())
            self.assertGreaterEqual(len(report["action_list"]), 3)

            summary_text = (output_dir / "latest_summary.md").read_text(encoding="utf-8")
            self.assertIn("Today's Action List", summary_text)
            self.assertIn("prod_alpha", summary_text)

            html_text = (output_dir / "latest_report.html").read_text(encoding="utf-8")
            self.assertIn("Daily review workflow", html_text)
            self.assertIn("Campaign export or fixture CSV", html_text)

    def test_alerts_and_wins_do_not_overlap(self) -> None:
        report = lam.build_report(
            lam.parse_args(
                [
                    "--csv-path",
                    str(FIXTURE_PATH),
                    "--monthly-budget",
                    "1000",
                ]
            )
        )
        alerted = {item["campaign_name"] for item in report["alerts"]}
        wins = {item["campaign_name"] for item in report["wins"]}
        self.assertTrue(alerted.isdisjoint(wins))

    def test_cli_requires_budget(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = lam.main(["--csv-path", str(FIXTURE_PATH)])
        self.assertEqual(exit_code, 1)
        self.assertIn("monthly budget", stderr.getvalue().lower())

    def test_missing_headers_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_csv = Path(tmpdir) / "bad.csv"
            bad_csv.write_text("Date,Campaign name,Impressions\n2026-03-03,test,1\n", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = lam.main(["--csv-path", str(bad_csv), "--monthly-budget", "1000"])
            self.assertEqual(exit_code, 1)
            self.assertIn("missing required csv headers", stderr.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
