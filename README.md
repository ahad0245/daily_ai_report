# Daily AI Applicant Processing Report

## Overview

This report adds a production-ready page for reviewing Zoho Recruit applications that:

- were created on a selected calendar date
- have `AI Processed = true`

The default date is yesterday in the configured application timezone.

## Verified project context

- Frontend framework currently used in this repository: none
- Implemented frontend approach: vanilla HTML, CSS, and JavaScript
- Existing API integration reused: Python `ZohoRecruitClient` / `UpdateZohoRecruitClient`
- Verified Applications fields:
  - application date: `Created_Time`
  - AI Processed filter: `AI_Processed`
  - application status: `Application_Status`
  - job numeric ID: `$Job_Opening_Id`
  - job public ID: `Job_Opening_ID`
  - job name: `Potential_Name`
  - application unique ID: `id`

## Folder structure

```text
daily_ai_report/
  app.py
  config.py
  report_service.py
  frontend/
    index.html
    styles.css
    app.js
  dist/
  .env.example
  README.md
scripts/
  build_daily_ai_report.py
```

## Requirements

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Environment variables

The report reuses the same Zoho credentials already used by the existing scripts.

New variables:

- `REPORT_APP_TIMEZONE`
- `REPORT_API_PAGE_SIZE`
- `REPORT_APP_HOST`
- `REPORT_APP_PORT`
- `REPORT_SELECTED_STATUS`
- `REPORT_REJECTED_STATUS`

See [daily_ai_report/.env.example](../daily_ai_report/.env.example).

## Run locally

```bash
python -m daily_ai_report.app
```

Then open:

```text
http://127.0.0.1:8000
```

## Production build

This frontend is plain static HTML/CSS/JS. Build copies deployable assets into `daily_ai_report/dist`.

```bash
python scripts/build_daily_ai_report.py
```

## Deployment notes

- No existing frontend hosting setup was found in this repository.
- The safest production deployment is to run `daily_ai_report.app` behind a reverse proxy or WSGI server.
- Because Zoho tokens must stay server-side, do not host this page as client-only static files unless the API routes are still served from a protected backend.
- Suggested production setup:
  - backend: Flask app behind Gunicorn/Waitress
  - reverse proxy: Nginx, Caddy, or IIS
  - static assets: `daily_ai_report/dist`

## API route

```text
GET /api/report/config
GET /api/report/daily-ai-applicant-processing?date=YYYY-MM-DD
```

## Features

- yesterday auto-selected on load
- timezone-aware previous-day logic
- paginated Zoho API fetch
- deduplication by application `id`
- grouping by `$Job_Opening_Id` with fallback handling
- selected vs rejected vs other status counts
- grand totals
- search
- column sorting
- CSV export
- loading, empty, and error states
- retry handling

