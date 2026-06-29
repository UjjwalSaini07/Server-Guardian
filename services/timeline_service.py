import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pymongo import DESCENDING

logger = logging.getLogger(__name__)

def log_event(
    service_id: str,
    event_type: str,
    severity: str,
    description: str,
    mongo_client,
    root_cause: Optional[dict] = None,
    duration: Optional[float] = None,
    metadata: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Log an event into the chronological observation timeline collection.
    """
    try:
        db = mongo_client["ServerAutomation"]
        timeline_col = db["monitoring_timeline"]
        
        # Build event document
        event_doc = {
            "timestamp": datetime.now(timezone.utc),
            "event_type": event_type,
            "severity": severity,
            "description": description,
            "related_service": service_id,
            "root_cause": root_cause,
            "duration": duration,
            "metadata": metadata or {}
        }
        
        timeline_col.insert_one(event_doc)
        
        # Ensure indexes exist
        timeline_col.create_index([("related_service", 1), ("timestamp", -1)])
        timeline_col.create_index([("timestamp", -1)])
        timeline_col.create_index([("event_type", 1)])
        
        logger.info(f"[TimelineService] Logged event: {event_type} for service {service_id}")
        return event_doc
    except Exception as e:
        logger.error(f"[TimelineService] Failed to log timeline event: {e}")
        return {}

def get_timeline(service_id: str, limit: int, mongo_client) -> List[Dict[str, Any]]:
    """
    Fetch chronological history for a specific service.
    """
    try:
        db = mongo_client["ServerAutomation"]
        timeline_col = db["monitoring_timeline"]
        
        query = {"related_service": service_id}
        events = list(
            timeline_col.find(query)
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )
        return [_serialize_event(e) for e in events]
    except Exception as e:
        logger.error(f"[TimelineService] Failed to fetch timeline for {service_id}: {e}")
        return []

def get_timeline_incidents(limit: int, mongo_client) -> List[Dict[str, Any]]:
    """
    Fetch timeline events filtered for outages, degradations, and recoveries.
    """
    try:
        db = mongo_client["ServerAutomation"]
        timeline_col = db["monitoring_timeline"]
        
        incident_types = [
            "SERVICE_DOWN", 
            "HEALTH_DEGRADED", 
            "LATENCY_INCREASED", 
            "DATABASE_WARNING", 
            "REDIS_FAILURE", 
            "RECOVERY_STARTED", 
            "HEALTHY", 
            "RECOVERED_AFTER_RETRY"
        ]
        query = {"event_type": {"$in": incident_types}}
        events = list(
            timeline_col.find(query)
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )
        return [_serialize_event(e) for e in events]
    except Exception as e:
        logger.error(f"[TimelineService] Failed to fetch incident timeline: {e}")
        return []

def get_all_timeline(limit: int, mongo_client) -> List[Dict[str, Any]]:
    """
    Fetch last N timeline events for all services.
    """
    try:
        db = mongo_client["ServerAutomation"]
        timeline_col = db["monitoring_timeline"]
        
        events = list(
            timeline_col.find()
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )
        return [_serialize_event(e) for e in events]
    except Exception as e:
        logger.error(f"[TimelineService] Failed to fetch all timeline: {e}")
        return []

def _serialize_event(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MongoDB timeline document into a JSON-serializable dict."""
    if not doc:
        return {}
    ts = doc.get("timestamp")
    ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    return {
        "id": str(doc.get("_id")),
        "timestamp": ts_str,
        "event_type": doc.get("event_type"),
        "severity": doc.get("severity"),
        "description": doc.get("description"),
        "related_service": doc.get("related_service"),
        "root_cause": doc.get("root_cause"),
        "duration": doc.get("duration"),
        "metadata": doc.get("metadata", {})
    }
