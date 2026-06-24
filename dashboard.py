import os
import threading
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient

from config import SERVICES_CONFIG, MONGO_URI
from services.monitoring_service import execute_ping
from services.scraper_service import run_scraper

app = FastAPI(title="Server Automation Keep-Alive Dashboard")

# Mount public directory for static assets
app.mount("/public", StaticFiles(directory="public"), name="public")

# Initialize MongoDB client
mongo_client = MongoClient(MONGO_URI)

@app.get("/api/status")
def get_status():
    """Return the status of all configured services fetched from MongoDB latest_status."""
    result = {}
    try:
        db = mongo_client["ServerAutomation"]
        status_docs = list(db["latest_status"].find())
        status_map = {doc["name"]: doc for doc in status_docs}
    except Exception as e:
        status_map = {}

    for s in SERVICES_CONFIG:
        name = s["name"]
        cached = status_map.get(name, {
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
    """Fetch recent execution logs from health_logs and monitoring_history, sorted descending."""
    logs = []
    try:
        db = mongo_client["ServerAutomation"]
        
        # Get last 50 health logs (detailed logs for pingers)
        health_col = db["health_logs"]
        health_records = list(health_col.find().sort("timestamp", -1).limit(50))
        
        # Get last 50 scraper history logs
        hist_col = db["monitoring_history"]
        scraper_records = list(hist_col.find({"service": "Stock Scraper"}).sort("timestamp", -1).limit(50))
        
        # Merge them
        all_records = []
        for r in health_records:
            all_records.append({
                "service": r.get("service", "Unknown"),
                "timestamp": r.get("timestamp"),
                "status": r.get("status"),
                "status_code": r.get("status_code"),
                "latency_ms": r.get("latency_ms", 0),
                "error": r.get("error"),
                "analytics": r.get("analytics")
            })
            
        for r in scraper_records:
            all_records.append({
                "service": r.get("service", "Unknown"),
                "timestamp": r.get("timestamp"),
                "status": r.get("status"),
                "status_code": None,
                "latency_ms": None,
                "error": r.get("error"),
                "analytics": None
            })
            
        # Sort merged list by timestamp descending and take the first 50
        all_records.sort(key=lambda x: x["timestamp"] if x["timestamp"] else datetime.min, reverse=True)
        recent_records = all_records[:50]
        
        for r in recent_records:
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
        # Run immediately in a FastAPI background task
        background_tasks.add_task(execute_ping, service, mongo_client)
        return {"status": "TRIGGERED", "message": f"Ping scheduled for {service_name}"}
        
    elif service["type"] == "scraper":
        # Run scraper in a background thread
        threading.Thread(target=run_scraper, args=(service, mongo_client)).start()
        return {"status": "TRIGGERED", "message": f"Scraping started in background for {service_name}"}

@app.get("/api/github-actions/status")
def get_github_actions_status():
    """Return the runtime status of the GitHub Action runner."""
    try:
        db = mongo_client["ServerAutomation"]
        status_doc = db["github_actions_status"].find_one({"_id": "runner_status"})
        if not status_doc:
            return {
                "last_execution_time": None,
                "last_success_time": None,
                "last_failure_time": None,
                "last_status": "UNKNOWN",
                "error": None
            }
            
        def serialize_dt(dt):
            return dt.isoformat() if isinstance(dt, datetime) else dt

        return {
            "last_execution_time": serialize_dt(status_doc.get("last_execution_time")),
            "last_success_time": serialize_dt(status_doc.get("last_success_time")),
            "last_failure_time": serialize_dt(status_doc.get("last_failure_time")),
            "last_status": status_doc.get("last_status", "UNKNOWN"),
            "error": status_doc.get("error")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query error: {str(e)}")

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the static glassmorphic dashboard HTML file."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Dashboard HTML Template not found</h1>", status_code=404)
