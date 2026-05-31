# Security Policy

## Supported Scope

This project is a local-first reporting utility. It is intended for deterministic analysis of LinkedIn Ads export data and optional AI-generated analyst notes when credentials are configured deliberately.

## Reporting A Vulnerability

If you find a security issue, do not open a public issue with exploit details.

Send:

- a short description of the issue
- affected file or workflow
- reproduction steps
- impact assessment

to the project maintainer through a private channel first.

## Current Security Boundaries

- The main KPI, pacing, and prioritization logic is deterministic.
- The optional AI note path should be treated as untrusted generated text.
- API keys must be provided through environment variables and never committed.
- The shipped demo artifact under `docs/sample-output/` is static synthetic sample output, not a live service endpoint.

## Out Of Scope

- Bugs in third-party services
- Spreadsheet content quality issues
- Local environment misconfiguration that does not create a security impact
