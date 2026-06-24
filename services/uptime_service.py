import logging
from datetime import datetime, timedelta, timezone

def calculate_uptime(service_id, mongo_client, days=None):
    """
    Calculate the uptime percentage and count checks for a given service.
    If days is None, calculate all-time uptime.
    """
    try:
        db = mongo_client["ServerAutomation"]
        col = db["monitoring_history"]
        
        query = {"service_id": service_id}
        
        if days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            query["timestamp"] = {"$gte": cutoff}
            
        records = list(col.find(query))
        
        total_checks = len(records)
        if total_checks == 0:
            return 100.0, 0, 0
            
        successful_checks = sum(1 for r in records if r.get("status") == "success")
        failed_checks = total_checks - successful_checks
        
        uptime_pct = (successful_checks / total_checks) * 100.0
        return round(uptime_pct, 2), successful_checks, failed_checks
    except Exception as e:
        logging.error(f"[UptimeService] Error calculating uptime for {service_id}: {e}")
        return 100.0, 0, 0

def get_reliability_rating(uptime_percentage):
    """
    Get the reliability score classification:
    100 = Excellent
    95-99 = Stable
    90-94 = Warning
    Below 90 = Critical
    """
    if uptime_percentage >= 100.0:
        return "Excellent"
    elif uptime_percentage >= 95.0:
        return "Stable"
    elif uptime_percentage >= 90.0:
        return "Warning"
    else:
        return "Critical"

def get_trend_indicator(service_id, mongo_client):
    """
    Determine if uptime is improving, stable, or degrading:
    ↑ Improving (24h > 7d)
    → Stable (24h == 7d)
    ↓ Degrading (24h < 7d)
    """
    uptime_24h, _, _ = calculate_uptime(service_id, mongo_client, days=1)
    uptime_7d, _, _ = calculate_uptime(service_id, mongo_client, days=7)
    
    if uptime_24h > uptime_7d:
        return "↑"
    elif uptime_24h < uptime_7d:
        return "↓"
    else:
        return "→"

def detect_consecutive_outages(service_id, mongo_client, threshold=3):
    """
    Check if the last N runs of the service have consecutively failed.
    """
    try:
        db = mongo_client["ServerAutomation"]
        col = db["monitoring_history"]
        
        # Get the last N records sorted by timestamp descending
        records = list(col.find({"service_id": service_id}).sort("timestamp", -1).limit(threshold))
        
        if len(records) < threshold:
            return False
            
        # Check if all of them are failures
        return all(r.get("status") == "failure" for r in records)
    except Exception as e:
        logging.error(f"[UptimeService] Error detecting consecutive outages for {service_id}: {e}")
        return False
