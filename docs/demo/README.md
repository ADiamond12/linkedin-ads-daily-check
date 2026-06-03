# LinkedIn Ads Daily Check Demo Storyboard

Use this storyboard for a short public-safe walkthrough. The sample campaign data is synthetic and committed for reproducible review.

## 60-Second Reviewer Flow

1. Open `docs/sample-output/daily-check-sample-report.html`.
2. Show the latest-date KPI cards for CPC, CTR, CPL, and conversion rate.
3. Show the month-to-date pacing section and explain whether spend is under, on, or over pace.
4. Open the control-risk register and explain which items should be resolved before budget changes.
5. Show daily movement and campaign movement to explain what changed since the previous reporting day.
6. Read the deterministic action list.
7. Scroll to the campaign-priority table and show that flagged campaigns include reasons.
8. Run `powershell -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1` to refresh the same report from the fixture.
9. Close with the workflow: CSV export -> KPI check -> movement -> risk register -> prioritized action list -> saved evidence.

The default screenshot uses `fixtures/sample_linkedin_ads.csv` so the walkthrough stays readable. Edge-case behavior is covered by `fixtures/edge_case_linkedin_ads.csv` and tests for spend with no clicks, high CPL, low CTR, and under-pacing.

## Screenshots To Capture

- `docs/screenshots/daily-check-report.png`: KPI cards, action list, and prioritized campaigns visible.

## What To Say

This is a compact marketing-ops control report. Its value is not model output; it is the repeatable daily artifact that replaces a manual spreadsheet scan, identifies review risks, compares movement, and keeps deterministic scoring separate from optional analyst notes.
