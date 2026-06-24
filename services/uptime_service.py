import logging
from datetime import datetime, timedelta, timezone

def get_service_uptime_percentage(service_name, mongo_client, limit=100):
    """Calculate uptime percentage based on the last N execution logs in monitoring_history."""
    try:
        db = mongo_client["ServerAutomation"]
        col = db["monitoring_history"]
        
        # Fetch last N records for this service
        records = list(col.find({"service": service_name}).sort("timestamp", -1).limit(limit))
        if not records:
            return 100.0
            
        success_count = sum(1 for r in records if r.get("status") in ("SUCCESS", "OK", "HEALTHY", "SKIPPED"))
        return (success_count / len(records)) * 100.0
    except Exception as e:
        logging.error(f"[UptimeService] Error calculating uptime percentage for {service_name}: {e}")
        return 100.0
