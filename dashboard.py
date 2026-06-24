import os
import threading
from datetime import datetime, timedelta, timezone
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
        
        # Fetch precomputed uptime metrics
        uptime_docs = list(db["uptime_metrics"].find())
        uptime_map = {doc["service_id"]: doc for doc in uptime_docs}
    except Exception as e:
        status_map = {}
        uptime_map = {}

    for s in SERVICES_CONFIG:
        name = s["name"]
        service_id = s.get("service_id")
        cached = status_map.get(name, {
            "status": "PENDING",
            "last_checked": None,
            "latency_ms": 0,
            "db_ok": True,
            "redis_ok": True
        })
        
        # Merge precomputed uptime data
        uptime_data = uptime_map.get(service_id, {})
        
        # Ensure timestamp is stringified
        last_checked_val = cached.get("last_checked")
        if isinstance(last_checked_val, datetime):
            last_checked_str = last_checked_val.isoformat()
        else:
            last_checked_str = last_checked_val

        result[name] = {
            "service_id": service_id,
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
            "details": cached.get("details", {}),
            
            # Merge precomputed uptime metrics
            "uptime_24h": uptime_data.get("uptime_24h", 100.0),
            "uptime_7d": uptime_data.get("uptime_7d", 100.0),
            "uptime_30d": uptime_data.get("uptime_30d", 100.0),
            "uptime_all_time": uptime_data.get("uptime_all_time", 100.0),
            "successful_checks": uptime_data.get("successful_checks", 0),
            "failed_checks": uptime_data.get("failed_checks", 0),
            "reliability_rating": uptime_data.get("reliability_rating", "Excellent"),
            "trend_indicator": uptime_data.get("trend_indicator", "→"),
            "consecutive_outages": uptime_data.get("consecutive_outages", False)
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
        scraper_records = list(hist_col.find({"service_id": "stock_scraper"}).sort("timestamp", -1).limit(50))
        
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
                "service": r.get("service_name", "Stock Scraper"),
                "timestamp": r.get("timestamp"),
                "status": "SUCCESS" if r.get("status") == "success" else "ERROR",
                "status_code": None,
                "latency_ms": r.get("latency_ms", 0),
                "error": r.get("failure_reason"),
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

@app.get("/api/services/{id}/uptime")
def get_service_uptime(id: str):
    """Return detailed uptime metrics, reliability scores, and trends for a specific service."""
    service = next((s for s in SERVICES_CONFIG if s.get("service_id") == id), None)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
        
    try:
        db = mongo_client["ServerAutomation"]
        col = db["uptime_metrics"]
        metrics = col.find_one({"service_id": id})
        
        if not metrics:
            return {
                "service_name": service["name"],
                "uptime_24h": 100.0,
                "uptime_7d": 100.0,
                "uptime_30d": 100.0,
                "uptime_all_time": 100.0,
                "successful_checks": 0,
                "failed_checks": 0,
                "reliability_rating": "Excellent",
                "trend_indicator": "→",
                "consecutive_outages": False
            }
            
        return {
            "service_name": metrics.get("service_name", service["name"]),
            "uptime_24h": metrics.get("uptime_24h", 100.0),
            "uptime_7d": metrics.get("uptime_7d", 100.0),
            "uptime_30d": metrics.get("uptime_30d", 100.0),
            "uptime_all_time": metrics.get("uptime_all_time", 100.0),
            "successful_checks": metrics.get("successful_checks", 0),
            "failed_checks": metrics.get("failed_checks", 0),
            "reliability_rating": metrics.get("reliability_rating", "Excellent"),
            "trend_indicator": metrics.get("trend_indicator", "→"),
            "consecutive_outages": metrics.get("consecutive_outages", False)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database fetch error: {str(e)}")

@app.get("/api/uptime/overview")
def get_uptime_overview():
    """Return overall platform reliability metrics."""
    try:
        from services.uptime_aggregator import get_platform_overview
        return get_platform_overview(mongo_client)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Aggregator error: {str(e)}")

@app.get("/api/uptime/history")
def get_uptime_history():
    """Return availability history grouped by day for the last 30 days."""
    try:
        db = mongo_client["ServerAutomation"]
        col = db["monitoring_history"]
        
        # Query last 30 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        records = list(col.find({"timestamp": {"$gte": cutoff}}))
        
        # Group by day and status
        daily_stats = {}
        for r in records:
            dt = r.get("timestamp")
            if not isinstance(dt, datetime):
                continue
            day_str = dt.strftime("%Y-%m-%d")
            status = r.get("status")
            
            if day_str not in daily_stats:
                daily_stats[day_str] = {"success": 0, "total": 0}
                
            daily_stats[day_str]["total"] += 1
            if status == "success":
                daily_stats[day_str]["success"] += 1
                
        # Format for charting
        sorted_days = sorted(daily_stats.keys())
        chart_data = []
        for day in sorted_days:
            success = daily_stats[day]["success"]
            total = daily_stats[day]["total"]
            pct = (success / total * 100.0) if total > 0 else 100.0
            chart_data.append({
                "date": day,
                "uptime": round(pct, 2),
                "total_checks": total
            })
            
        return chart_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query history: {str(e)}")

@app.get("/api/alerts")
def get_alerts():
    """Return active incidents and alert history logs from MongoDB."""
    try:
        db = mongo_client["ServerAutomation"]
        
        # Query active incidents from alert_state
        active_states = list(db["alert_state"].find({"active_incident": True}))
        active_list = []
        for state in active_states:
            # Find the corresponding SERVICE_DOWN alert to get details
            down_alert = db["alerts"].find_one({
                "service_id": state["service_id"],
                "alert_type": "SERVICE_DOWN"
            }, sort=[("created_at", -1)])
            
            active_list.append({
                "service_id": state["service_id"],
                "service_name": down_alert.get("service_name") if down_alert else state["service_id"],
                "incident_started_at": state["incident_started_at"].isoformat() if isinstance(state.get("incident_started_at"), datetime) else state.get("incident_started_at"),
                "message": down_alert.get("message", "Service is down") if down_alert else "Service is down"
            })
            
        # Query last 100 historical alerts
        history = list(db["alerts"].find().sort("created_at", -1).limit(100))
        history_list = []
        for a in history:
            dt = a.get("created_at")
            dt_str = dt.isoformat() if isinstance(dt, datetime) else str(dt)
            history_list.append({
                "service_id": a.get("service_id"),
                "service_name": a.get("service_name"),
                "alert_type": a.get("alert_type"),
                "severity": a.get("severity", "info"),
                "message": a.get("message"),
                "created_at": dt_str,
                "sent": a.get("sent", False)
            })
            
        return {
            "active_incidents": active_list,
            "history": history_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch alerts: {str(e)}")

@app.get("/api/alerts/analytics")
def get_alerts_analytics():
    """Return alerts analytics metrics."""
    try:
        db = mongo_client["ServerAutomation"]
        alerts_col = db["alerts"]
        
        total_alerts = alerts_col.count_documents({})
        
        # Alerts per service
        pipeline = [
            {"$group": {"_id": "$service_name", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        agg_results = list(alerts_col.aggregate(pipeline))
        alerts_per_service = {r["_id"]: r["count"] for r in agg_results if r["_id"]}
        
        # MTTR calculation
        recover_alerts = list(alerts_col.find({
            "alert_type": "SERVICE_RECOVERED",
            "downtime_seconds": {"$exists": True}
        }))
        
        if recover_alerts:
            avg_mttr = sum(a["downtime_seconds"] for a in recover_alerts) / len(recover_alerts)
        else:
            avg_mttr = 0.0
            
        # MTTD placeholder (constant 150 seconds/2.5 mins based on 5 mins checks frequency)
        avg_mttd = 150.0 if total_alerts > 0 else 0.0
        
        # Incident trends (count of SERVICE_DOWN alerts per day in the last 7 days)
        trend_pipeline = [
            {"$match": {
                "alert_type": "SERVICE_DOWN",
                "created_at": {"$gte": datetime.now(timezone.utc) - timedelta(days=7)}
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "count": {"$sum": 1}
            }},
            {"$sort": {"_id": 1}}
        ]
        trend_results = list(alerts_col.aggregate(trend_pipeline))
        incident_trends = {r["_id"]: r["count"] for r in trend_results}
        
        return {
            "total_alerts": total_alerts,
            "alerts_per_service": alerts_per_service,
            "mean_time_to_detect_seconds": avg_mttd,
            "mean_time_to_recover_seconds": round(avg_mttr, 1),
            "incident_trends": incident_trends
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch alerts analytics: {str(e)}")

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the static glassmorphic dashboard HTML file."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse(content="<h1>Dashboard HTML Template not found</h1>", status_code=404)
