# LinkedIn Ads Daily Check Demo Storyboard

Use this storyboard for a short public-safe walkthrough. The sample campaign data is synthetic and committed for reproducible review.

## 60-Second Reviewer Flow

1. Open `docs/sample-output/daily-check-sample-report.html`.
2. Show the latest-date KPI cards for CPC, CTR, CPL, and conversion rate.
3. Show the month-to-date pacing section and explain whether spend is under, on, or over pace.
4. Read the deterministic action list.
5. Scroll to the campaign-priority table and show that flagged campaigns include reasons.
6. Run `powershell -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1` to refresh the same report from the fixture.
7. Close with the workflow: CSV export -> KPI check -> pacing -> prioritized action list -> saved evidence.

## Screenshots To Capture

- `docs/screenshots/daily-check-report.png`: KPI cards, action list, and prioritized campaigns visible.

## What To Say

This is a compact marketing-ops utility. Its value is not model output; it is the repeatable daily artifact that replaces a manual spreadsheet scan and keeps deterministic scoring separate from optional analyst notes.
