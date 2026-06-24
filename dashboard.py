import os
import threading
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient, ASCENDING, DESCENDING

from config import SERVICES_CONFIG, MONGO_URI
from services.monitoring_service import execute_ping
from services.scraper_service import run_scraper
from services.analytics_service import (
    get_uptime,
    get_latency_stats,
    get_trend,
    get_service_ranking,
    get_reliability_report,
    get_platform_summary,
)

app = FastAPI(title="Server Automation Keep-Alive Dashboard")

# Mount public directory for static assets
app.mount("/public", StaticFiles(directory="public"), name="public")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Initialize MongoDB client
mongo_client = MongoClient(MONGO_URI)


def _ensure_analytics_indexes():
    """
    Ensure MongoDB indexes required for analytics queries exist.
    Called once at startup – idempotent (create_index is a no-op if the index
    already exists).
    """
    try:
        col = mongo_client["ServerAutomation"]["monitoring_history"]
        # Compound index: service-specific range queries (most common)
        col.create_index(
            [("service_id", ASCENDING), ("timestamp", DESCENDING)],
            name="service_id_timestamp",
            background=True,
        )
        # Single-field index: platform-wide range queries (trend charts)
        col.create_index(
            [("timestamp", DESCENDING)],
            name="timestamp_desc",
            background=True,
        )
    except Exception as _idx_err:
        import logging
        logging.getLogger(__name__).warning("Could not create analytics indexes: %s", _idx_err)


_ensure_analytics_indexes()


def _service_name_for(service_id: str) -> str:
    """Look up the human-readable service name for a given service_id."""
    for s in SERVICES_CONFIG:
        if s.get("service_id") == service_id:
            return s["name"]
    return service_id


def _require_service(service_id: str):
    """Raise 404 if service_id is not in SERVICES_CONFIG."""
    if not any(s.get("service_id") == service_id for s in SERVICES_CONFIG):
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")

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


# ===========================================================================
# /api/analytics/* – Historical Analytics Engine (Phase 1)
# All routes are additive. No existing route is modified.
# ===========================================================================

@app.get("/api/analytics/uptime/{service_id}")
def analytics_uptime(service_id: str, days: int = Query(30, ge=1, le=365)):
    """
    Return uptime statistics for a single service over the last `days` days.

    Sample response:
    {
      "service_id": "stock_sentinel",
      "service_name": "Stock Sentinel Server",
      "days": 30,
      "uptime_pct": 99.7,
      "total_checks": 300,
      "success_checks": 299,
      "failure_checks": 1,
      "sla_target_pct": 99.9,
      "sla_met": false
    }
    """
    _require_service(service_id)
    try:
        name = _service_name_for(service_id)
        return get_uptime(service_id, name, mongo_client, days=days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/analytics/latency/{service_id}")
def analytics_latency(service_id: str, days: int = Query(30, ge=1, le=365)):
    """
    Return full latency distribution (avg, min, max, median, P95, P99)
    for a single service over the last `days` days.

    Sample response:
    {
      "service_id": "stock_sentinel",
      "service_name": "Stock Sentinel Server",
      "days": 30,
      "percentiles": {
        "avg_ms": 843.4,
        "min_ms": 312.1,
        "max_ms": 4120.0,
        "median_ms": 790.0,
        "p95_ms": 2100.5,
        "p99_ms": 3800.0,
        "sample_count": 287
      }
    }
    """
    _require_service(service_id)
    try:
        name = _service_name_for(service_id)
        return get_latency_stats(service_id, name, mongo_client, days=days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/analytics/trend/{service_id}")
def analytics_trend(service_id: str, days: int = Query(30, ge=1, le=365)):
    """
    Return daily uptime trend for a single service over the last `days` days.
    Each entry represents one calendar day (UTC).

    Sample response:
    [
      {
        "date": "2026-05-25",
        "uptime_pct": 100.0,
        "total_checks": 288,
        "success_checks": 288,
        "failure_checks": 0,
        "avg_latency_ms": 712.3
      },
      ...
    ]
    """
    _require_service(service_id)
    try:
        return get_trend(service_id, mongo_client, days=days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/analytics/ranking")
def analytics_ranking(days: int = Query(30, ge=1, le=365)):
    """
    Return all services ranked by uptime percentage (best → worst)
    over the last `days` days. Useful for service comparison and
    executive summaries.

    Sample response:
    {
      "days": 30,
      "ranked_services": [
        {
          "rank": 1,
          "service_id": "stock_sentinel",
          "service_name": "Stock Sentinel Server",
          "uptime_pct": 99.9,
          "total_checks": 300,
          "reliability_rating": "Excellent",
          "trend_indicator": "→"
        },
        ...
      ],
      "best_service_id": "stock_sentinel",
      "worst_service_id": "affiliate_health"
    }
    """
    try:
        return get_service_ranking(mongo_client, days=days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/analytics/reliability/{service_id}")
def analytics_reliability(service_id: str):
    """
    Return a comprehensive reliability report for a single service.
    Includes multi-window uptime (24h / 7d / 30d / all-time), trend,
    consecutive outage detection, and P95/P99 latency.

    Sample response:
    {
      "service_id": "stock_sentinel",
      "service_name": "Stock Sentinel Server",
      "uptime_24h": 100.0,
      "uptime_7d": 99.8,
      "uptime_30d": 99.5,
      "uptime_all_time": 99.3,
      "total_checks": 4320,
      "success_checks": 4299,
      "failure_checks": 21,
      "reliability_rating": "Excellent",
      "trend_indicator": "↑",
      "consecutive_outages": false,
      "latency_30d": {
        "avg_ms": 843.4,
        "min_ms": 312.1,
        "max_ms": 4120.0,
        "median_ms": 790.0,
        "p95_ms": 2100.5,
        "p99_ms": 3800.0,
        "sample_count": 287
      },
      "sla_target_pct": 99.9,
      "sla_met_30d": false
    }
    """
    _require_service(service_id)
    try:
        name = _service_name_for(service_id)
        return get_reliability_report(service_id, name, mongo_client)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/analytics/platform")
def analytics_platform(days: int = Query(30, ge=1, le=365)):
    """
    Return a platform-wide summary across all services.
    Structured for executive reporting and dashboard overview widgets.

    Sample response:
    {
      "generated_at": "2026-06-24T09:50:00.000000+00:00",
      "window_days": 30,
      "overall_uptime_pct": 98.7,
      "total_checks": 1200,
      "total_failures": 16,
      "best_service": "stock_sentinel",
      "worst_service": "affiliate_health",
      "services_above_sla": 2,
      "services_below_sla": 2,
      "avg_latency_ms": 921.0,
      "p95_latency_ms": 2840.0,
      "service_summaries": [ ... ]
    }
    """
    try:
        return get_platform_summary(mongo_client, days=days)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ===========================================================================
# Phase 3 — Incident Timeline Engine
# ===========================================================================

@app.get("/api/incidents")
def list_incidents(
    service_id: str = Query(None),
    status: str = Query(None),
    limit: int = Query(50, ge=1, le=200)
):
    """
    List incidents with optional filters.
    Query params: service_id, status (open|acknowledged|resolved), limit
    """
    try:
        from services.incident_service import get_incident_history
        return get_incident_history(
            service_id=service_id,
            status=status,
            limit=limit,
            mongo_client=mongo_client
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/incidents/metrics")
def incident_metrics(days: int = Query(30, ge=1, le=365)):
    """
    Return MTTD, MTTR, and incident counts for the last `days` days.
    """
    try:
        from services.incident_service import get_incident_metrics
        return get_incident_metrics(days=days, mongo_client=mongo_client)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    """Return a single incident by its ID including full event timeline."""
    try:
        from services.incident_service import get_incident_by_id
        doc = get_incident_by_id(incident_id, mongo_client)
        if not doc:
            raise HTTPException(status_code=404, detail="Incident not found")
        return doc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str, acknowledged_by: str = Query("dashboard")):
    """
    Mark an open incident as acknowledged.
    Optionally pass ?acknowledged_by=<name> to attribute the acknowledgment.
    """
    try:
        from services.incident_service import acknowledge_incident as ack_fn
        success = ack_fn(
            incident_id=incident_id,
            acknowledged_by=acknowledged_by,
            mongo_client=mongo_client
        )
        if not success:
            raise HTTPException(status_code=404, detail="Incident not found or not in open state")
        return {"status": "acknowledged", "incident_id": incident_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ===========================================================================
# Phase 3 — Public Status Page
# ===========================================================================

@app.get("/api/status-page/summary")
def get_status_summary():
    """
    Return a JSON snapshot suitable for the public status page.
    Includes overall status, per-service current state, active incidents, and recent resolved incidents.
    """
    try:
        db = mongo_client["ServerAutomation"]

        # Latest status per service
        latest_docs = list(db["latest_status"].find())
        services_out = []
        any_down = False
        any_degraded = False

        for doc in latest_docs:
            raw_status = doc.get("status", "UNKNOWN")
            is_success = raw_status == "SUCCESS"
            is_skipped = raw_status == "SKIPPED"

            if not is_success and not is_skipped:
                any_down = True
            elif is_skipped:
                any_degraded = True

            current_status = "operational" if is_success else ("skipped" if is_skipped else "down")

            # Fetch 30d uptime from uptime_metrics cache
            svc_id = next(
                (s["service_id"] for s in SERVICES_CONFIG if s["name"] == doc.get("name")),
                None
            )
            uptime_30d = 100.0
            avg_lat = None
            if svc_id:
                metrics = db["uptime_metrics"].find_one({"service_id": svc_id})
                if metrics:
                    uptime_30d = metrics.get("uptime_30d", 100.0)
                avg_lat_doc = db["monitoring_history"].find_one(
                    {"service_id": svc_id, "latency_ms": {"$ne": None}},
                    sort=[("timestamp", -1)]
                )
                if avg_lat_doc:
                    avg_lat = avg_lat_doc.get("latency_ms")

            services_out.append({
                "service_id": svc_id,
                "service_name": doc.get("name", svc_id),
                "current_status": current_status,
                "uptime_30d": round(uptime_30d, 2),
                "avg_latency_ms": round(avg_lat, 1) if avg_lat else None,
                "last_checked": doc.get("last_checked").isoformat() if isinstance(doc.get("last_checked"), datetime) else doc.get("last_checked"),
            })

        overall = "operational"
        if any_down:
            down_count = sum(1 for s in services_out if s["current_status"] == "down")
            overall = "major_outage" if down_count >= len(services_out) else "partial_outage"
        elif any_degraded:
            overall = "degraded"

        # Active incidents
        from services.incident_service import get_incident_history
        active_incidents = get_incident_history(None, "open", 10, mongo_client)
        active_incidents += get_incident_history(None, "acknowledged", 10, mongo_client)

        # Recent resolved incidents (last 30)
        recent_resolved = get_incident_history(None, "resolved", 30, mongo_client)

        return {
            "overall_status": overall,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "services": services_out,
            "active_incidents": active_incidents,
            "recent_incidents": recent_resolved,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/status", response_class=HTMLResponse)
def serve_status_page():
    """Serve the public status page (server-rendered, no CDN dependencies)."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, "templates", "status.html")

    try:
        import json
        # Fetch live data
        summary = get_status_summary()

        with open(html_path, "r", encoding="utf-8") as f:
            template = f.read()

        # Inject JSON data for server-side rendering
        summary_json = json.dumps(summary)
        html = template.replace("__STATUS_DATA__", summary_json)
        return HTMLResponse(content=html)
    except Exception as e:
        return HTMLResponse(
            content=f"<h1>Status page temporarily unavailable</h1><p>{e}</p>",
            status_code=503
        )


# ===========================================================================
# Phase 3 — Executive Reporting
# ===========================================================================

@app.get("/api/reports/weekly")
def report_weekly():
    """Generate and return the latest 7-day executive report."""
    try:
        from services.report_service import generate_weekly_report
        return generate_weekly_report(mongo_client)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/reports/monthly")
def report_monthly():
    """Generate and return the latest 30-day executive report."""
    try:
        from services.report_service import generate_monthly_report
        return generate_monthly_report(mongo_client)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/reports/benchmarks")
def report_benchmarks():
    """Return service benchmarking table (uptime rank, latency rank, incident rank)."""
    try:
        from services.report_service import generate_benchmarks
        return generate_benchmarks(mongo_client)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
