# Service Monitor Renewal

Service Monitor is an internal operations system that connects customer-service intake, CX analysis, exchanges and returns, quality inspection, laboratory handoff, and final-result tracking in one workflow.

## Key features

- Product, serial number, LOT, and transfer-number based case tracking
- Role-, team-, product-, and action-based access control
- CS/CX, exchange, quality, laboratory, and result workflows
- Operational dashboards for volume, progress, loss reasons, Jira issues, LOT, uptime, and quality
- Slack text parsing, Google Sheets synchronization, and Jira REST API integration
- Excel import/export with normalization and validation
- Server incident and downtime management

## Tech stack

- Python 3.12, FastAPI, SQLAlchemy, Jinja2
- JavaScript, HTML, CSS
- Pandas, OpenPyXL, SQLite
- Google Sheets API and Jira REST API

## Local setup

1. Create and activate a Python virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and fill in the required values.
4. Start the application with `uvicorn app.main:app --reload`.

The local SQLite database is created automatically. Runtime databases, uploads, generated reports, and credentials are intentionally excluded from Git.

## Tests

Run the test suite with `pytest`.
