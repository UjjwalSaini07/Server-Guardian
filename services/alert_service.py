import logging
from config import SERVICES_CONFIG
from services.notification_service import process_alert

# Configurable thresholds
ALERT_CONFIG = {
    "high_latency_ms": 3000.0,
    "consecutive_failures": 3,
    "critical_uptime_threshold": 95.0
}

def evaluate_ping_result(service, result, mongo_client):
    """
    Evaluates a single ping execution outcome and raises alerts if thresholds are crossed.
    """
    try:
        service_id = service["service_id"]
        name = service["name"]
        
        status = result.get("status")
        latency = result.get("latency_ms", 0.0)
        db_ok = result.get("db_ok", True)
        redis_ok = result.get("redis_ok", True)
        error_msg = result.get("error")
        url = service.get("url", "")
        
        is_success = (status == "SUCCESS")
        
        # 1. Evaluate Service Down / Recovery
        if not is_success:
            # Service Down Alert
            reason = error_msg or f"HTTP Status Code {result.get('status_code', 'Unknown')}"
            process_alert(
                service_id=service_id,
                service_name=name,
                alert_type="SERVICE_DOWN",
                severity="critical",
                message=f"Service {name} is down: {reason}",
                details={"url": url, "reason": reason},
                mongo_client=mongo_client
            )
            
            # Check for Consecutive Failures
            from services.uptime_service import detect_consecutive_outages
            consecutive = detect_consecutive_outages(service_id, mongo_client, threshold=ALERT_CONFIG["consecutive_failures"])
            if consecutive:
                process_alert(
                    service_id=service_id,
                    service_name=name,
                    alert_type="CONSECUTIVE_FAILURES",
                    severity="critical",
                    message=f"Service {name} has failed consecutively {ALERT_CONFIG['consecutive_failures']} times.",
                    details={"failures_count": ALERT_CONFIG["consecutive_failures"]},
                    mongo_client=mongo_client
                )
        else:
            # Check for Recovery
            db = mongo_client["ServerAutomation"]
            state_doc = db["alert_state"].find_one({"service_id": service_id}) or {}
            
            if state_doc.get("active_incident", False):
                # Service Recovered Alert
                process_alert(
                    service_id=service_id,
                    service_name=name,
                    alert_type="SERVICE_RECOVERED",
                    severity="success",
                    message=f"Service {name} has recovered and is back online.",
                    details={"status": status},
                    mongo_client=mongo_client
                )
                
            # 2. Check for High Latency
            if latency > ALERT_CONFIG["high_latency_ms"]:
                process_alert(
                    service_id=service_id,
                    service_name=name,
                    alert_type="HIGH_LATENCY",
                    severity="warning",
                    message=f"Service {name} latency ({latency:.0f}ms) is above threshold.",
                    details={"latency": latency, "threshold": ALERT_CONFIG["high_latency_ms"]},
                    mongo_client=mongo_client
                )
                
        # 3. Check Database and Cache Sub-components failures
        db = mongo_client["ServerAutomation"]
        state_doc = db["alert_state"].find_one({"service_id": service_id}) or {}
        
        # Database checks
        if not db_ok:
            if not state_doc.get("db_failed", False):
                db["alert_state"].update_one({"service_id": service_id}, {"$set": {"db_failed": True}}, upsert=True)
                process_alert(
                    service_id=service_id,
                    service_name=name,
                    alert_type="DATABASE_FAILURE",
                    severity="warning",
                    message=f"Database backend is failing for service: {name}",
                    details={},
                    mongo_client=mongo_client
                )
        else:
            if state_doc.get("db_failed", False):
                db["alert_state"].update_one({"service_id": service_id}, {"$set": {"db_failed": False}}, upsert=True)
                process_alert(
                    service_id=service_id,
                    service_name=name,
                    alert_type="API_FAILURE",
                    severity="success",
                    message=f"Database backend recovered for service: {name}",
                    details={},
                    mongo_client=mongo_client
                )
                
        # Cache checks
        if not redis_ok:
            if not state_doc.get("cache_failed", False):
                db["alert_state"].update_one({"service_id": service_id}, {"$set": {"cache_failed": True}}, upsert=True)
                process_alert(
                    service_id=service_id,
                    service_name=name,
                    alert_type="CACHE_FAILURE",
                    severity="warning",
                    message=f"Cache backend is failing for service: {name}",
                    details={},
                    mongo_client=mongo_client
                )
        else:
            if state_doc.get("cache_failed", False):
                db["alert_state"].update_one({"service_id": service_id}, {"$set": {"cache_failed": False}}, upsert=True)
                process_alert(
                    service_id=service_id,
                    service_name=name,
                    alert_type="API_FAILURE",
                    severity="success",
                    message=f"Cache backend recovered for service: {name}",
                    details={},
                    mongo_client=mongo_client
                )

        # 4. Check Uptime / Health Score Degradation
        from services.uptime_service import calculate_uptime
        uptime_30d, _, _ = calculate_uptime(service_id, mongo_client, days=30)
        
        if uptime_30d < ALERT_CONFIG["critical_uptime_threshold"]:
            process_alert(
                service_id=service_id,
                service_name=name,
                alert_type="HEALTH_SCORE_DEGRADED",
                severity="warning",
                message=f"Health score dropped below critical threshold: {uptime_30d:.2f}%",
                details={"uptime": uptime_30d, "threshold": ALERT_CONFIG["critical_uptime_threshold"]},
                mongo_client=mongo_client
            )
        else:
            if state_doc.get("uptime_degraded", False):
                process_alert(
                    service_id=service_id,
                    service_name=name,
                    alert_type="HEALTH_SCORE_RECOVERED",
                    severity="success",
                    message=f"Health score restored to healthy state: {uptime_30d:.2f}%",
                    details={"uptime": uptime_30d, "threshold": ALERT_CONFIG["critical_uptime_threshold"]},
                    mongo_client=mongo_client
                )
                
    except Exception as e:
        logging.error(f"[AlertService] Error evaluating ping result: {e}")

def evaluate_scraper_result(service, result, mongo_client):
    """
    Evaluates stock scraper run results.
    """
    try:
        service_id = service["service_id"]
        name = service["name"]
        status = result.get("status")
        error_msg = result.get("error")
        
        if status == "ERROR":
            process_alert(
                service_id=service_id,
                service_name=name,
                alert_type="MONITORING_FAILURE",
                severity="warning",
                message=f"Stock scraper task failed: {error_msg}",
                details={"reason": error_msg},
                mongo_client=mongo_client
            )
    except Exception as e:
        logging.error(f"[AlertService] Error evaluating scraper result: {e}")
