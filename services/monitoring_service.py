import time
import requests
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pymongo import MongoClient, ASCENDING
from config import SERVICES_CONFIG, HISTORY_CLEANUP_DAYS

MONITOR_VERSION = "1.0"

def is_within_allowed_hours(allowed_hours_ist, allowed_days):
    """Check if current time is within active hours (IST) and active days."""
    ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    
    # Check weekday
    if allowed_days is not None and ist_now.weekday() not in allowed_days:
        return False
        
    # Check hours
    if allowed_hours_ist is None:
        return True
        
    hour = ist_now.hour
    start, end = allowed_hours_ist
    
    if start <= end:
        return start <= hour < end
    else:
        # Crosses midnight (e.g. 9 AM to 2 AM next day)
        return hour >= start or hour < end

def init_db_indexes(mongo_client):
    """Create TTL index on expireAt field for all pinger log collections."""
    logging.info("[MonitoringService] Initializing MongoDB TTL indexes...")
    for s in SERVICES_CONFIG:
        if s["type"] == "pinger":
            try:
                db = mongo_client[s["db_name"]]
                collection = db[s["collection_name"]]
                collection.create_index([("expireAt", ASCENDING)], expireAfterSeconds=0)
                logging.info(f"[MonitoringService] TTL index verified on {s['db_name']}.{s['collection_name']}")
            except Exception as e:
                logging.error(f"[MonitoringService] Failed to create TTL index for {s['name']}: {e}")

    # Initialize TTL index on monitoring_history to purge records older than HISTORY_CLEANUP_DAYS
    try:
        db = mongo_client["ServerAutomation"]
        col = db["monitoring_history"]
        expected_seconds = HISTORY_CLEANUP_DAYS * 24 * 3600
        existing_indexes = col.index_information()
        
        recreate_ttl = False
        if "timestamp_ttl" in existing_indexes:
            current_expire = existing_indexes["timestamp_ttl"].get("expireAfterSeconds")
            if current_expire != expected_seconds:
                logging.info(f"[MonitoringService] Recreating TTL index on monitoring_history because retention days changed from {current_expire // (24 * 3600)} to {HISTORY_CLEANUP_DAYS}...")
                col.drop_index("timestamp_ttl")
                recreate_ttl = True
        else:
            recreate_ttl = True
            
        if recreate_ttl:
            col.create_index([("timestamp", ASCENDING)], name="timestamp_ttl", expireAfterSeconds=expected_seconds)
            logging.info(f"[MonitoringService] TTL index verified on monitoring_history for {HISTORY_CLEANUP_DAYS} days retention.")
    except Exception as e:
        logging.error(f"[MonitoringService] Failed to create TTL index on monitoring_history: {e}")

def execute_ping(service, mongo_client):
    """Ping a service, record latency, parse metrics, and save to MongoDB collections."""
    from services.analytics_service import parse_health_json
    from services.health_check_service import evaluate_health_score
    
    name = service["name"]
    url = service["url"]
    db_name = service["db_name"]
    col_name = service["collection_name"]
    parse_analytics = service.get("parse_analytics", False)
    
    if not is_within_allowed_hours(service["allowed_hours_ist"], service["allowed_days"]):
        logging.info(f"[MonitoringService] Skipping ping for {name} (outside active hours/days).")
        now = datetime.now(timezone.utc)
        
        status_data = {
            "name": name,
            "type": "pinger",
            "url": url,
            "status": "SKIPPED",
            "message": "Outside active schedule",
            "last_checked": now,
            "latency_ms": 0,
            "db_ok": True,
            "redis_ok": True
        }
        
        history_data = {
            "service_id": service.get("service_id", "quillix_api"),
            "service_name": name,
            "timestamp": now,
            "status": "success",
            "latency_ms": 0,
            "failure_reason": "Skipped (Outside active schedule)"
        }
        
        try:
            db = mongo_client["ServerAutomation"]
            db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
            db["monitoring_history"].insert_one(history_data)
        except Exception as e:
            logging.error(f"[MonitoringService] Failed to update skipped status in DB for {name}: {e}")
        return status_data
        
    now = datetime.now(timezone.utc)
    expire_time = now + timedelta(hours=service.get("log_expiry_hours", 1))
    
    max_attempts = 3
    attempt = 0
    backoff_delay = 5
    response = None
    last_exception = None
    latency = 0
    
    while attempt < max_attempts:
        attempt += 1
        attempt_start_time = time.time()
        try:
            response = requests.get(url, timeout=60)
            status_code = response.status_code
            latency = (time.time() - attempt_start_time) * 1000
            
            # Retry only on 502, 503, 504 (standard gateway/sleep/spin-up errors)
            if status_code == 200 or (status_code < 502 or status_code > 504):
                break
            logging.warning(f"[MonitoringService] Ping attempt {attempt} for {name} returned status {status_code}. Retrying in {backoff_delay}s...")
        except requests.exceptions.RequestException as e:
            logging.warning(f"[MonitoringService] Ping attempt {attempt} for {name} failed with connection error: {e}. Retrying in {backoff_delay}s...")
            response = None
            last_exception = e
            latency = (time.time() - attempt_start_time) * 1000
            
        if attempt < max_attempts:
            time.sleep(backoff_delay)
            backoff_delay *= 2

    if response is None:
        raise last_exception if last_exception else requests.exceptions.RequestException("Max ping retries exceeded.")
        
    try:
        status_code = response.status_code
        status = "SUCCESS" if status_code == 200 else f"FAILED: {status_code}"
        
        response_text = response.text
        parsed_json = None
        analytics = {}
        
        try:
            parsed_json = response.json()
            if parse_analytics:
                analytics = parse_health_json(name, parsed_json)
        except Exception:
            pass
            
        log_doc = {
            "service": name,
            "timestamp": now,
            "status": status,
            "status_code": status_code,
            "latency_ms": latency,
            "response": response_text[:1000],
            "expireAt": expire_time
        }
        
        if parse_analytics and analytics:
            log_doc["analytics"] = analytics
            
        # Write to detailed logs
        db = mongo_client[db_name]
        db[col_name].insert_one(log_doc)
        
        logging.info(f"[MonitoringService] {name} -> {status} ({latency:.1f}ms)")
        
        # Write to latest status
        status_data = {
            "name": name,
            "type": "pinger",
            "url": url,
            "status": status,
            "status_code": status_code,
            "latency_ms": latency,
            "last_checked": now,
            "db_ok": analytics.get("db_ok", True) if parse_analytics else True,
            "redis_ok": analytics.get("redis_ok", True) if parse_analytics else True,
            "uptime_seconds": analytics.get("uptime_seconds") if parse_analytics else None,
            "details": analytics.get("details", {}) if parse_analytics else {}
        }
        
        is_success = status_code == 200

        # Deep health scoring via health_check_service (Phase 3A)
        health_score, health_detail = evaluate_health_score(
            response_json=parsed_json,
            status_code=status_code,
            latency_ms=latency,
            service_config=service,
        )

        history_data = {
            # -- Core fields (unchanged) --
            "service_id":     service.get("service_id", "quillix_api"),
            "service_name":   name,
            "timestamp":      now,
            "status":         "success" if is_success else "failure",
            "status_code":    status_code,
            "latency_ms":     latency,
            "failure_reason": None if is_success else f"HTTP status code {status_code}",
            # -- Extended optional fields --
            "health_score":      health_score,
            "health_detail":     health_detail,
            "response_size":     len(response_text) if response_text else None,
            "response_time_ms":  latency,
            "api_status":        is_success,
            "database_status":   health_detail.get("db_ok"),
            "cache_status":      health_detail.get("cache_ok"),
            "error_type":        None,
            "monitor_version":   MONITOR_VERSION,
        }
        
        sa_db = mongo_client["ServerAutomation"]
        sa_db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
        sa_db["monitoring_history"].insert_one(history_data)
        
        # Evaluate alert rules
        try:
            from services.alert_service import evaluate_ping_result
            evaluate_ping_result(service, status_data, mongo_client)
        except Exception as alert_err:
            logging.error(f"[MonitoringService] Alert evaluation failed for {name}: {alert_err}")
        
        return status_data
        
    except requests.exceptions.RequestException as e:
        latency = (time.time() - start_time) * 1000
        logging.error(f"[MonitoringService] {name} -> CONNECTION ERROR: {e}")
        
        log_doc = {
            "service": name,
            "timestamp": now,
            "status": "ERROR",
            "status_code": None,
            "latency_ms": latency,
            "error": str(e),
            "expireAt": expire_time
        }
        
        # Write to detailed logs
        db = mongo_client[db_name]
        db[col_name].insert_one(log_doc)
        
        # Write to latest status
        status_data = {
            "name": name,
            "type": "pinger",
            "url": url,
            "status": "ERROR",
            "status_code": None,
            "latency_ms": latency,
            "last_checked": now,
            "db_ok": False,
            "redis_ok": False,
            "error": str(e),
            "details": {}
        }
        
        history_data = {
            # -- Core fields (unchanged) --
            "service_id":     service.get("service_id", "quillix_api"),
            "service_name":   name,
            "timestamp":      now,
            "status":         "failure",
            "status_code":    None,
            "latency_ms":     latency,
            "failure_reason": str(e),
            # -- Extended optional fields --
            "health_score":     0,
            "response_size":    None,
            "response_time_ms": latency,
            "api_status":       False,
            "database_status":  None,
            "cache_status":     None,
            "error_type":       type(e).__name__,
            "monitor_version":  MONITOR_VERSION,
        }
        
        try:
            sa_db = mongo_client["ServerAutomation"]
            sa_db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
            sa_db["monitoring_history"].insert_one(history_data)
            
            # Evaluate alert rules for error condition
            from services.alert_service import evaluate_ping_result
            evaluate_ping_result(service, status_data, mongo_client)
        except Exception as db_err:
            logging.error(f"[MonitoringService] Failed to write error status or evaluate alerts in DB: {db_err}")
            
        return status_data
