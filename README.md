<div align="center">

# ⚡ ServerGuardian Pro

**Production-grade observability & incident response platform**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Automated-2088FF?style=for-the-badge&logo=github-actions&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

> Transform raw operational data into actionable intelligence — with real-time monitoring, deep health scoring, incident lifecycle management, executive reporting, and a stunning glassmorphism dashboard.

</div>

## 📖 Table of Contents

- [What is ServerGuardian Pro?](#-what-is-serverguardian-pro)
- [Platform Capabilities](#-platform-capabilities)
- [Architecture](#-architecture)
- [Dashboard Overview](#-dashboard-overview)
- [Repository Structure](#-repository-structure)
- [Data Model](#-data-model)
- [API Reference](#-api-reference)
- [Configuration](#-configuration)
- [GitHub Actions Setup](#-github-actions-setup)
- [Local Development](#-local-development)
- [Docker Deployment](#-docker-deployment)
- [Public Status Page](#-public-status-page)
- [Troubleshooting](#-troubleshooting)
- [Security](#-security)
- [Author](#-author)


## 🔍 What is Server Guardian?

Server Guardian is a self-hosted, production-oriented observability platform built in Python. It continuously monitors HTTP health endpoints, scores service quality, detects and tracks incidents, sends intelligent alerts, generates executive reports, and serves a live operator dashboard — all backed by MongoDB and automated through GitHub Actions.

It is purpose-built around four pillars:

| Pillar | Description |
|---|---|
| **Monitor** | Scheduled HTTP health checks via GitHub Actions cron (every 5 min) |
| **Detect** | Multi-dimensional health scoring + automatic incident creation |
| **Alert** | SMTP email notifications with deduplication, recovery alerts, and rate limiting |
| **Observe** | Real-time glassmorphism dashboard + public status page + executive reports |


## 🚀 Platform Capabilities

### 🔁 Automated Monitoring
- Scheduled HTTP health checks for each configured service (every 5 minutes via GitHub Actions)
- Latency measurement in milliseconds per request
- HTTP status code classification: `SUCCESS`, `FAILED`, `ERROR`, `SKIPPED`
- Service schedule enforcement using IST (Asia/Kolkata) timezone
- Per-service concurrency via Python threading

### 🧠 Deep Health Check Engine
- Multi-dimensional health scoring (0–100 composite score) per check:
  - **HTTP validation** — status code, response time
  - **JSON schema validation** — checks expected keys in the response body
  - **Database sub-component** — parses nested `db` health signals
  - **Cache sub-component** — parses nested `cache` health signals
  - **Latency scoring** — configurable WARNING and CRITICAL thresholds
- Health deductions logged with reason codes for every degradation

### 🚨 Intelligent Alerting
- Alert types: `SERVICE_DOWN`, `SERVICE_RECOVERED`, `HIGH_LATENCY`, `DB_FAILURE`, `CACHE_FAILURE`, `HEALTH_SCORE_DEGRADED`
- Duplicate suppression — no spam during sustained outages
- Recovery notifications with downtime duration
- Configurable rate-limiting per service
- All alerts persisted in MongoDB regardless of email availability

### 📋 Incident Lifecycle Engine
- Incidents automatically created on first failure detection
- Full lifecycle: `open` → `acknowledged` → `resolved`
- MTTD (Mean Time to Detect) computed from monitoring history
- MTTR (Mean Time to Resolve) computed on resolution
- Timeline events logged for every state transition
- Incident acknowledgment via dashboard button

### 📊 Analytics Engine
- Per-service and platform-wide aggregation:
  - Uptime % over 7, 14, 30-day windows
  - Latency percentiles: avg, min, max, median, P95, P99
  - Daily uptime trend series (30-day)
  - Cross-service reliability leaderboard with trend indicators
  - Platform executive summary (total checks, failure rate, avg latency)
- All analytics powered by MongoDB aggregation pipelines (no Python-side data loading)
- Reliability thresholds: `Excellent ≥ 99.5%`, `Good ≥ 97%`, `Warning ≥ 90%`, `Critical < 90%`

### 📈 Executive Reporting
- **Weekly reports** — 7-day service performance summary with SLA compliance
- **Monthly reports** — 30-day executive summary with MTTR, incidents, and benchmarks
- **Service benchmarking** — uptime rank, latency rank, incident frequency rank per service
- Reports delivered via email and accessible via API
- Auto-dispatched by `monitor_runner.py` on the configured UTC day/hour

### 🌐 Public Status Page
- Standalone `/status` route — no authentication required
- Shows overall system health, per-service uptime, latency, last checked time
- Lists active incidents and 30-day incident history table
- Auto-refreshes every 60 seconds
- All data server-rendered (single JSON injection, no extra API calls)

### 📈 Stock Scraper (Optional)
- Scrapes Indian stock market data from Screener.in
- Respects weekday and IST trading-hour constraints
- Stores results in `investiqdb.Stocks` MongoDB collection
- Toggled via `ENABLE_STOCK_SCRAPER` environment variable


## 🏗️ Architecture

### System Overview

```mermaid
graph TD
    Browser[Operator Browser] -->|HTTP :8080| Dashboard[FastAPI Dashboard]
    PublicUser[Public User] -->|GET /status| Dashboard

    Dashboard -->|Read| Mongo[(MongoDB Atlas)]
    Dashboard -->|Render| Templates[Jinja2 Templates]

    GHA[GitHub Actions - Every 5 min] -->|Runs| Runner[monitor_runner.py]
    Runner -->|Ping| Services[Monitored HTTP Services]
    Runner -->|Write| Mongo
    Runner -->|Evaluate| AlertEngine[Alert & Incident Engine]
    AlertEngine -->|Send| SMTP[SMTP Email]
    AlertEngine -->|Write| Mongo

    Runner -->|Optional| Scraper[Stock Scraper]
    Scraper -->|Upsert| InvestiqDB[(investiqdb.Stocks)]
```

### Monitoring Lifecycle (Sequence)

```mermaid
sequenceDiagram
    participant GH as GitHub Actions
    participant MR as monitor_runner.py
    participant MS as monitoring_service
    participant HC as health_check_service
    participant IS as incident_service
    participant AL as alert_service
    participant NS as notification_service
    participant DB as MongoDB

    GH->>MR: Trigger scheduled run (every 5 min)
    MR->>MS: execute_ping(service_config)
    MS->>HC: compute_health_score(response)
    HC-->>MS: HealthCheckResult (0–100 score)
    MS->>DB: Write latest_status + monitoring_history
    MS->>AL: evaluate_ping_result(result, service)
    AL->>IS: open_incident() or resolve_incident()
    IS->>DB: Write incidents collection
    AL->>NS: dispatch_alert_if_needed()
    NS->>DB: Write alerts collection
    NS-->>GH: Send email via SMTP (if configured)
    MR->>DB: Aggregate uptime_metrics cache
    MR->>DB: Write github_actions_status
```

### Deployment Topology

```mermaid
graph TD
    Repo[GitHub Repository] -->|Push| GHA[GitHub Actions]
    Repo -->|Deploy| Render[FastAPI on Render / VPS]
    GHA -->|Cron every 5 min| Runner[monitor_runner.py on ubuntu-latest]
    Runner -->|Read/Write| Atlas[(MongoDB Atlas)]
    Render -->|Read| Atlas
    Render -->|Serve| Browser[Operator & Public Users]
```


## 🖥️ Dashboard Overview

The operator dashboard at `http://localhost:8080` (or your deployed URL) provides:

| Section | Description |
|---|---|
| **Live Service Tiles** | Real-time status, uptime %, latency, last-checked time (IST 12hr) |
| **Monitor Engine** | GitHub Actions runner status, freshness indicator, last success/failure |
| **Analytics Engine** | Platform health overview with uptime, latency, failure rate KPIs |
| **Latency Intelligence** | Per-service average, P95, P99 latency with trend |
| **Reliability Leaderboard** | Cross-service ranking table with trend indicators |
| **Platform Health Charts** | 30-day uptime trend + success/failure stacked bar chart |
| **Incident Timeline** | Active and historical incidents with MTTD, MTTR, severity, status |
| **Alert History Log** | Chronological alert feed with service, type, reason, timestamp |
| **Event Stream Console** | Terminal-style live log of all dashboard activity |
| **Executive Reports** | Weekly/monthly report KPIs + service benchmarking table |
| **Public Status Page** | Separate `/status` route for public-facing uptime visibility |

All timestamps are displayed in **IST (Asia/Kolkata) timezone in 12-hour AM/PM format**.


## 📁 Repository Structure

```
ServerGuardian/
│
├── main.py                          # Entrypoint — starts FastAPI + creates MongoDB indexes
├── dashboard.py                     # FastAPI app — all routes, UI and API (29 endpoints)
├── monitor_runner.py                # GitHub Actions runner — ping, alert, report dispatch
├── config.py                        # SERVICES_CONFIG, env-driven thresholds, health schemas
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Container image definition
├── docker-compose.yml               # Compose setup for local containerized run
│
├── services/
│   ├── monitoring_service.py        # HTTP ping execution, result persistence
│   ├── health_check_service.py      # Multi-dimensional health scoring engine (0–100)
│   ├── analytics_service.py         # All analytics aggregation pipelines
│   ├── alert_service.py             # Alert rule evaluation and type classification
│   ├── notification_service.py      # Alert state management, email dispatch coordination
│   ├── incident_service.py          # Incident lifecycle: open / acknowledge / resolve
│   ├── report_service.py            # Weekly/monthly report generation and benchmarking
│   ├── email_provider.py            # SMTP email templates and sending
│   ├── uptime_service.py            # Uptime helpers and outage detection
│   ├── uptime_aggregator.py         # Cached uptime metrics aggregation
│   └── scraper_service.py           # Stock scraping job wrapper
│
├── models/
│   ├── analytics.py                 # Pydantic models: UptimeStats, LatencyStats, etc.
│   ├── health.py                    # Pydantic models: HealthCheckResult, HealthDeduction
│   └── incidents.py                 # Pydantic models: Incident, IncidentMetrics, Timeline
│
├── servers/
│   ├── stock_scraper_service.py     # Screener.in scraper implementation
│   └── IndianStockTicker.json       # List of stock tickers to track
│
├── templates/
│   ├── index.html                   # Main operator dashboard (glassmorphism UI)
│   └── status.html                  # Public-facing status page (/status route)
│
├── static/
│   └── js/
│       ├── charts.js                # Shared Chart.js helpers (ChartHelpers namespace)
│       └── incidents.js             # Incident timeline + executive reports JS engine
│
├── public/                          # Static public assets (images, icons)
│
└── .github/
    └── workflows/
        └── serverguardian-monitor.yml  # GitHub Actions cron workflow
```


## 🗄️ Data Model

All runtime data is stored in **MongoDB**. Two databases are used:

### `ServerAutomation` Database

| Collection | Purpose |
|---|---|
| `latest_status` | Current visible state for each service (1 doc per service) |
| `monitoring_history` | Long-term check history — source of truth for all analytics |
| `health_logs` | Detailed ping logs with response snippets (TTL-expired) |
| `uptime_metrics` | Cached multi-window uptime values (7d, 14d, 30d) |
| `alerts` | Full alert history with type, reason, delivery status |
| `alert_state` | Incident suppression context and recovery tracking |
| `incidents` | Incident lifecycle documents with timeline events |
| `github_actions_status` | Last runner execution result and timestamps |

### `investiqdb` Database

| Collection | Purpose |
|---|---|
| `Stocks` | Scraped stock snapshots (price, market cap, P/E, ROCE, ROE, etc.) |

### Key Document Shapes

**`latest_status`**
```json
{
  "service_id": "quillix_api",
  "service_name": "Quillix API",
  "status": "failed",
  "health_score": 0,
  "latency_ms": 1589,
  "http_status_code": 503,
  "last_checked": "2026-06-24T16:13:26+00:00",
  "uptime_7d": 91.3,
  "uptime_30d": 95.7
}
```

**`incidents`**
```json
{
  "incident_id": "INC-2026-0042",
  "service_id": "quillix_api",
  "service_name": "Quillix API",
  "status": "open",
  "severity": "critical",
  "started_at": "2026-06-24T03:31:46+00:00",
  "acknowledged_at": null,
  "resolved_at": null,
  "mttd_seconds": 312,
  "mttr_seconds": null,
  "timeline": [
    { "event": "INCIDENT_OPENED", "timestamp": "...", "note": "HTTP 503" }
  ]
}
```

## 📡 API Reference

The dashboard exposes **29 endpoints** across 6 functional groups.

### UI Routes

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | Operator dashboard HTML |
| `GET` | `/status` | Public status page HTML |
| `GET` | `/static/*` | JavaScript assets |
| `GET` | `/public/*` | Public images and icons |

### Status & Logs

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/status` | Latest state for all configured services |
| `GET` | `/api/logs` | Merged recent activity feed (health logs + alerts) |
| `POST` | `/api/ping/{service_name}` | Trigger a background ping or scraper run |
| `GET` | `/api/github-actions/status` | Last GitHub Actions runner state |

### Uptime & Alerts

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/services/{id}/uptime` | Cached uptime metrics for one service |
| `GET` | `/api/uptime/overview` | Overall platform reliability summary |
| `GET` | `/api/uptime/history` | 30-day daily availability series |
| `GET` | `/api/alerts` | Active incidents + recent alert history |
| `GET` | `/api/alerts/analytics` | Alert counts and recovery rate metrics |

### Analytics

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/analytics/platform?days=30` | Platform-wide health overview KPIs |
| `GET` | `/api/analytics/uptime/{service_id}?days=30` | Per-service uptime stats |
| `GET` | `/api/analytics/latency/{service_id}?days=30` | Per-service latency percentiles |
| `GET` | `/api/analytics/trend/{service_id}?days=30` | Daily uptime trend series |
| `GET` | `/api/analytics/ranking?days=30` | Cross-service reliability leaderboard |
| `GET` | `/api/analytics/reliability/{service_id}` | Full reliability report for one service |

### Incidents

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/incidents?limit=20` | List incidents (most recent first) |
| `GET` | `/api/incidents/{id}` | Full incident detail with timeline |
| `POST` | `/api/incidents/{id}/acknowledge` | Acknowledge an open incident |
| `GET` | `/api/incidents/metrics?days=30` | MTTD, MTTR, total incidents aggregated |

### Executive Reports

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/reports/weekly` | 7-day performance report JSON |
| `GET` | `/api/reports/monthly` | 30-day executive summary JSON |
| `GET` | `/api/reports/benchmarks` | Cross-service ranking and SLA compliance |

### Public Status API

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/status-page/summary` | Summary JSON powering the public status page |


## ⚙️ Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
# ── Required ────────────────────────────────────────────────
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true

# ── Monitored Service URLs ───────────────────────────────────
QUILLIX_API_URL=https://your-service-1.com/health
AFFILIATE_HEALTH_URL=https://your-service-2.com/health
STOCK_SENTINEL_URL=https://your-service-3.com/health
VISIONRETAIL_IQ_URL=https://your-service-4.com/health

# ── Email Alerting (Optional) ────────────────────────────────
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=alerts@yourdomain.com
EMAIL_PASSWORD=your-app-password
EMAIL_FROM=ServerGuardian <alerts@yourdomain.com>
ALERT_RECIPIENTS=ops@yourdomain.com,dev@yourdomain.com

# ── Feature Flags ────────────────────────────────────────────
ENABLE_STOCK_SCRAPER=false

# ── Reliability Thresholds ───────────────────────────────────
RELIABILITY_EXCELLENT=99.5
RELIABILITY_GOOD=97.0
RELIABILITY_WARNING=90.0
SLA_TARGET_PCT=99.5

# ── Health Check Latency Thresholds (ms) ─────────────────────
HEALTH_LATENCY_WARNING_MS=2000
HEALTH_LATENCY_CRITICAL_MS=5000

# ── Incident Settings ─────────────────────────────────────────
INCIDENT_ID_PREFIX=INC

# ── Report Dispatch ──────────────────────────────────────────
REPORT_SEND_DAY=0          # 0 = Monday (ISO weekday)
REPORT_SEND_HOUR_UTC=6     # Send at 06:00 UTC = 11:30 AM IST
```

> If email variables are incomplete, alerts are still logged to MongoDB — email delivery is skipped safely without crashing the runner.

### Configuring Services (`config.py`)

Services are defined in the `SERVICES_CONFIG` list. Each entry supports:

```python
{
    "service_id": "my_service",
    "name": "My Service",
    "url": os.getenv("MY_SERVICE_URL"),
    "type": "pinger",                   # or "scraper"
    "enabled": True,
    "allowed_hours": (0, 24),           # IST hour range (0,24) = 24/7
    "allowed_days": list(range(7)),     # 0=Mon … 6=Sun
    "health_schema": {                  # Optional: expected JSON keys to validate
        "status": str,
        "database": dict
    }
}
```


## 🤖 GitHub Actions Setup

The workflow file is at `.github/workflows/serverguardian-monitor.yml`.

### What it does
- Runs **every 5 minutes** via `schedule: cron: '*/5 * * * *'`
- Supports **manual trigger** via `workflow_dispatch`
- Installs Python 3.11 + all dependencies from `requirements.txt`
- Runs `python monitor_runner.py`

### Required Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Description |
|---|---|
| `MONGO_URI` | MongoDB Atlas connection string |
| `QUILLIX_API_URL` | Health endpoint URL for Quillix API |
| `AFFILIATE_HEALTH_URL` | Health endpoint URL for Affiliate service |
| `STOCK_SENTINEL_URL` | Health endpoint URL for Stock Sentinel |
| `VISIONRETAIL_IQ_URL` | Health endpoint URL for VisionRetail IQ |
| `EMAIL_HOST` | SMTP host (e.g. `smtp.gmail.com`) |
| `EMAIL_PORT` | SMTP port (e.g. `587`) |
| `EMAIL_USER` | SMTP username / sending address |
| `EMAIL_PASSWORD` | SMTP password or app password |
| `EMAIL_FROM` | Friendly sender name + address |
| `ENABLE_STOCK_SCRAPER` | `true` or `false` |
| `ALERT_RECIPIENTS` | Comma-separated recipient email list |

> **Note:** If `MONGO_URI` is missing or empty, the runner exits immediately with `Empty host` error. Always verify secrets are populated.

### Trigger a Manual Run

```
GitHub → Actions → ServerGuardian Monitor → Run workflow
```


## 💻 Local Development

### Prerequisites

- Python 3.11+
- MongoDB Atlas cluster **or** local MongoDB (`mongod` running)
- Network access to your monitored service URLs

### Install

```bash
git clone https://github.com/UjjwalSaini07/Server-Automation.git
```
```bash
cd Server-Automation
```
```bash
python -m pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env with your MONGO_URI and service URLs
```

### Run the Dashboard

```bash
python main.py
```

Opens at → `http://localhost:8080`  
Public status page → `http://localhost:8080/status`

### Run One Monitoring Cycle Manually

```bash
python monitor_runner.py
```

This is identical to what GitHub Actions runs on schedule. Use it to test your secrets and service connectivity locally.


## 🐳 Docker Deployment

### Build and Start

```bash
docker compose up --build
```

### What the container does

- Runs `python main.py` (FastAPI dashboard)
- Exposes port `8080`
- Reads environment variables from your host or a `.env` file mounted via Compose

> **Note:** The monitoring runner (`monitor_runner.py`) is designed to run in GitHub Actions, not inside Docker. The Docker container serves only the dashboard.


## 🌐 Public Status Page

The `/status` route serves a standalone, public-facing uptime page — no login required.

**What it shows:**
- Overall system status banner (Operational / Partial Outage / Major Outage)
- Per-service health: status dot, uptime %, average latency, last checked (IST)
- Active incidents with severity and acknowledgment status
- 30-day incident history table with MTTR

**Designed to be linked externally** — share `https://your-domain.com/status` with your customers so they can self-serve during outages instead of flooding your support channel.


## 🔧 Troubleshooting

### Dashboard Shows `PENDING` for All Services
- Verify `MONGO_URI` is set and the cluster is reachable
- Run `python monitor_runner.py` locally once to populate `latest_status`
- Check that service URLs in `config.py` / `.env` are correct and reachable

### GitHub Actions Fails: `Empty host (or extra comma in host list)`
- `MONGO_URI` secret is empty or missing in GitHub repository secrets
- Go to **Settings → Secrets → Actions** and add/update `MONGO_URI`

### Email Alerts Not Sending
- Confirm `EMAIL_HOST`, `EMAIL_USER`, `EMAIL_PASSWORD`, `EMAIL_FROM`, `ALERT_RECIPIENTS` are all set
- For Gmail: use an [App Password](https://support.google.com/accounts/answer/185833), not your login password
- Verify `ALERT_RECIPIENTS` contains at least one valid address

### Analytics / Charts Show No Data
- `monitoring_history` must have at least a few documents with matching `service_id`
- Ensure the dashboard and runner both point to the **same MongoDB cluster**
- Check that `uptime_metrics` collection is being populated (written at end of each runner cycle)

### Monitoring Is Stale (`Freshness: Xm ago`)
- GitHub Actions has not run for `X` minutes — check the Actions tab for errors
- Common causes: empty secrets, workflow disabled, GitHub free-tier concurrent job limits

### Stock Scraper Does Nothing
- Set `ENABLE_STOCK_SCRAPER=true` in your environment / secrets
- Verify current IST time is within the configured `allowed_hours` window
- Check `servers/IndianStockTicker.json` exists and is valid JSON

### Incidents Not Being Created
- Incidents are created by `incident_service.py` when `notification_service.py` detects a new failure
- Confirm `monitor_runner.py` is completing full cycles without Python exceptions
- Check the `alert_state` collection — incidents are only opened when no existing open incident exists for that service


## 🔒 Security

- **Never commit `.env`** — it is listed in `.gitignore`
- Use **GitHub Secrets** for all runner credentials — never hardcode them in workflow files
- Use **MongoDB Atlas IP Access Rules** to restrict database access to GitHub Actions IP ranges or your server IP
- Health endpoints are treated as **untrusted inputs** — response parsing is wrapped in try/except
- The `/status` route exposes **only aggregated, non-sensitive data** — no internal errors, no credentials
- Dashboard routes do not expose raw MongoDB queries or connection details to the browser


## 👤 Author

**Ujjwal Saini**  
*Founder · Lead Engineer · System Architect*

Passionate Full-Stack Engineer specializing in AI-powered systems, real-time analytics, secure database designs, and scalable platform architectures. ServerGuardian Pro is designed and built end-to-end — from MongoDB aggregation pipelines and GitHub Actions scheduling to the incident engine, executive reports, and the glassmorphism operator dashboard.

[![Portfolio](https://img.shields.io/badge/Portfolio-ujjwalsaini.vercel.app-6366f1?style=for-the-badge&logoColor=white)](https://ujjwalsaini.vercel.app)
[![GitHub](https://img.shields.io/badge/GitHub-UjjwalSaini07-181717?style=for-the-badge&logo=github)](https://github.com/UjjwalSaini07)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-ujjwalsaini07-0A66C2?style=for-the-badge&logo=linkedin)](https://www.linkedin.com/in/ujjwalsaini07)
[![Email](https://img.shields.io/badge/Email-Contact-EA4335?style=for-the-badge&logo=gmail)](mailto:ujjwalsaini0007+server@gmail.com)


## 📄 License

This project is distributed under the **MIT License**. See [LICENSE](./LICENSE) for full terms.

<div align="center">

Built with ⚡ by **Ujjwal Saini** · Powered by FastAPI + MongoDB + GitHub Actions

</div>
