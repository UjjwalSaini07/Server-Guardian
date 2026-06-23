import os
import threading
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from config import SERVICES_CONFIG
from runner import LATEST_STATUS, execute_ping, execute_scrape, mongo_client

app = FastAPI(title="Server Automation Keep-Alive Dashboard")

from fastapi.staticfiles import StaticFiles

app.mount("/public", StaticFiles(directory="public"), name="public")

@app.get("/api/status")
def get_status():
    """Return the cached in-memory status of all configured services."""
    result = {}
    for s in SERVICES_CONFIG:
        name = s["name"]
        cached = LATEST_STATUS.get(name, {
            "status": "PENDING",
            "last_checked": None,
            "latency_ms": 0,
            "db_ok": True,
            "redis_ok": True
        })
        
        # Ensure timestamp is stringified
        last_checked_val = cached.get("last_checked")
        if isinstance(last_checked_val, datetime):
            last_checked_str = last_checked_val.isoformat()
        else:
            last_checked_str = last_checked_val

        result[name] = {
            "name": name,
            "type": s["type"],
            "url": s.get("url"),
            "interval_minutes": s["interval_minutes"],
            "allowed_hours_ist": s.get("allowed_hours_ist"),
            "allowed_days": s.get("allowed_days"),
            "status": cached.get("status"),
            "status_code": cached.get("status_code"),
            "latency_ms": cached.get("latency_ms"),
            "last_checked": last_checked_str,
            "db_ok": cached.get("db_ok", True),
            "redis_ok": cached.get("redis_ok", True),
            "uptime_seconds": cached.get("uptime_seconds"),
            "error": cached.get("error"),
            "details": cached.get("details", {})
        }
    return result

@app.get("/api/logs")
def get_logs():
    """Fetch recent ping logs from the unified ServerAutomation health_logs collection."""
    logs = []
    try:
        db = mongo_client["ServerAutomation"]
        col = db["health_logs"]
        records = col.find().sort("timestamp", -1).limit(50)
        for r in records:
            ts = r["timestamp"]
            ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
            
            logs.append({
                "service": r.get("service", "Unknown"),
                "timestamp": ts_str,
                "status": r["status"],
                "status_code": r.get("status_code"),
                "latency_ms": r.get("latency_ms", 0),
                "error": r.get("error"),
                "analytics": r.get("analytics")
            })
    except Exception as e:
        logs.append({
            "service": "System",
            "timestamp": datetime.utcnow().isoformat(),
            "status": "ERROR",
            "error": f"Database fetch error: {str(e)}"
        })
    return logs

@app.post("/api/ping/{service_name}")
def trigger_ping(service_name: str, background_tasks: BackgroundTasks):
    """Force run a ping or scraper job immediately in the background."""
    service = next((s for s in SERVICES_CONFIG if s["name"] == service_name), None)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
        
    if service["type"] == "pinger":
        # Run immediately in a background task so we don't block the request
        background_tasks.add_task(execute_ping, service)
        return {"status": "TRIGGERED", "message": f"Ping scheduled for {service_name}"}
        
    elif service["type"] == "scraper":
        # Run scraper in a background thread
        threading.Thread(target=execute_scrape, args=(service,)).start()
        return {"status": "TRIGGERED", "message": f"Scraping started in background for {service_name}"}

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the static glassmorphic dashboard HTML file."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Dashboard HTML Template not found</h1>", status_code=404)
