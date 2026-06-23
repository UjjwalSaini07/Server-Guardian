import time
import requests
import logging
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient, ASCENDING
from zoneinfo import ZoneInfo
from config import MONGO_URI, SERVICES_CONFIG
from servers.stock_scraper_service import scrape_stocks

# Global in-memory dictionary to store the latest health status of each service
LATEST_STATUS = {}

# Initialize MongoDB client
mongo_client = MongoClient(MONGO_URI)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

def init_db_indexes():
    """Create TTL index on expireAt field for all pinger log collections."""
    logging.info("[Runner] Initializing MongoDB TTL indexes...")
    for s in SERVICES_CONFIG:
        if s["type"] == "pinger":
            try:
                db = mongo_client[s["db_name"]]
                collection = db[s["collection_name"]]
                # Index document will expire when expireAt is reached (expireAfterSeconds=0)
                collection.create_index([("expireAt", ASCENDING)], expireAfterSeconds=0)
                logging.info(f"[Runner] TTL index verified on {s['db_name']}.{s['collection_name']}")
            except Exception as e:
                logging.error(f"[Runner] Failed to create TTL index for {s['name']}: {e}")

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

def parse_health_json(service_name, data):
    """Parse nested health metrics from known endpoints for advanced analytics."""
    parsed = {
        "db_ok": True,
        "redis_ok": True,
        "uptime_seconds": None,
        "details": {}
    }
    
    if not isinstance(data, dict):
        return parsed
        
    try:
        if service_name == "Stock Sentinel Server":
            # Stock Sentinel format
            parsed["uptime_seconds"] = data.get("uptime_seconds")
            services = data.get("services", {})
            
            mongo_status = services.get("mongodb", {})
            parsed["db_ok"] = mongo_status.get("status") == "healthy"
            parsed["details"]["mongodb_latency_ms"] = mongo_status.get("latency_ms")
            
            redis_status = services.get("redis", {})
            parsed["redis_ok"] = redis_status.get("status") == "healthy"
            parsed["details"]["redis_latency_ms"] = redis_status.get("latency_ms")
            
            parsed["details"]["system"] = data.get("system", {})
            parsed["details"]["environment"] = data.get("environment", {})
            
        elif service_name == "VisionRetail IQ":
            # VisionRetail format
            parsed["uptime_seconds"] = data.get("uptime_seconds")
            parsed["db_ok"] = data.get("db_ok", True)
            parsed["redis_ok"] = data.get("redis_ok", True)
            parsed["details"]["redis_latency_ms"] = data.get("redis_latency_ms", 0.0)
            parsed["details"]["processing_latency_ms"] = data.get("processing_latency_ms")
            parsed["details"]["event_ingestion_rate_epm"] = data.get("event_ingestion_rate_epm")
            parsed["details"]["cameras"] = data.get("cameras", [])
            parsed["details"]["stores"] = data.get("stores", [])
            parsed["details"]["status_overall"] = data.get("status", "unknown")
            
    except Exception as e:
        logging.error(f"[Runner] Error parsing custom metrics for {service_name}: {e}")
        
    return parsed

def execute_ping(service):
    """Ping a service, record latency, parse metrics, and save to MongoDB."""
    name = service["name"]
    url = service["url"]
    db_name = service["db_name"]
    col_name = service["collection_name"]
    parse_analytics = service.get("parse_analytics", False)
    
    if not is_within_allowed_hours(service["allowed_hours_ist"], service["allowed_days"]):
        logging.info(f"[Runner] Skipping ping for {name} (outside active hours/days).")
        # Update latest status in-memory as "paused" or "skipped"
        LATEST_STATUS[name] = {
            "status": "SKIPPED",
            "message": "Outside active schedule",
            "last_checked": datetime.now(timezone.utc),
            "latency_ms": 0
        }
        return
        
    now = datetime.now(timezone.utc)
    # Expiry set to 1 hour (default)
    expire_time = now + timedelta(hours=service.get("log_expiry_hours", 1))
    
    db = mongo_client[db_name]
    collection = db[col_name]
    
    start_time = time.time()
    try:
        # 60s timeout to survive Render spin-up/cold-starts
        response = requests.get(url, timeout=60)
        latency = (time.time() - start_time) * 1000
        
        status_code = response.status_code
        status = "SUCCESS" if status_code == 200 else f"FAILED: {status_code}"
        
        response_text = response.text
        parsed_json = None
        analytics = {}
        
        # Try to parse json response
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
            "response": response_text[:1000],  # cap response body in db
            "expireAt": expire_time
        }
        
        if parse_analytics and analytics:
            log_doc["analytics"] = analytics
            
        collection.insert_one(log_doc)
        
        logging.info(f"[Runner] {name} -> {status} ({latency:.1f}ms)")
        
        # Update cache
        LATEST_STATUS[name] = {
            "status": status,
            "status_code": status_code,
            "latency_ms": latency,
            "last_checked": now,
            "db_ok": analytics.get("db_ok", True) if parse_analytics else True,
            "redis_ok": analytics.get("redis_ok", True) if parse_analytics else True,
            "uptime_seconds": analytics.get("uptime_seconds") if parse_analytics else None,
            "details": analytics.get("details", {}) if parse_analytics else {}
        }
        
    except requests.exceptions.RequestException as e:
        latency = (time.time() - start_time) * 1000
        logging.error(f"[Runner] {name} -> CONNECTION ERROR: {e}")
        
        log_doc = {
            "service": name,
            "timestamp": now,
            "status": "ERROR",
            "status_code": None,
            "latency_ms": latency,
            "error": str(e),
            "expireAt": expire_time
        }
        collection.insert_one(log_doc)
        
        # Update cache
        LATEST_STATUS[name] = {
            "status": "ERROR",
            "status_code": None,
            "latency_ms": latency,
            "last_checked": now,
            "db_ok": False,
            "redis_ok": False,
            "error": str(e)
        }

def execute_scrape(service):
    """Run the stock scraping job."""
    name = service["name"]
    if not is_within_allowed_hours(service["allowed_hours_ist"], service["allowed_days"]):
        logging.info(f"[Runner] Skipping stock scraper (outside active hours/days).")
        LATEST_STATUS[name] = {
            "status": "SKIPPED",
            "message": "Outside scraping hours (Mon-Fri 9:30-16:00 IST)",
            "last_checked": datetime.now(timezone.utc)
        }
        return
        
    try:
        start_time = datetime.now(timezone.utc)
        LATEST_STATUS[name] = {
            "status": "RUNNING",
            "last_checked": start_time
        }
        # Run scraper service
        result = scrape_stocks(mongo_client)
        
        LATEST_STATUS[name] = {
            "status": "SUCCESS" if result.get("status") == "completed" else "SKIPPED",
            "last_checked": datetime.now(timezone.utc),
            "details": result
        }
    except Exception as e:
        logging.error(f"[Runner] Stock Scraper encountered an error: {e}")
        LATEST_STATUS[name] = {
            "status": "ERROR",
            "last_checked": datetime.now(timezone.utc),
            "error": str(e)
        }
