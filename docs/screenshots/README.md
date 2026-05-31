# Screenshots

This folder stores public-safe screenshots used by the GitHub README and portfolio site.

Current capture:

- `daily-check-report.png`: curated sample HTML report generated from `fixtures/sample_linkedin_ads.csv`.

Refresh flow:

```powershell
python .\linkedin_ads_monitor.py --config .\config.example.json
```

Copy `output/latest_report.html` to `docs/sample-output/daily-check-sample-report.html`, open it locally, and capture the first report viewport. Do not commit private campaign exports or generated `output/` files.
