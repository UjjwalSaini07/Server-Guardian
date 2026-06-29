"""
services/incident_service.py

ServerGuardian Incident Timeline Engine.

Manages the full lifecycle of service incidents:
  open → acknowledged → resolved

Each incident gets a human-readable ID (INC-YYYY-NNN), computed MTTD/MTTR,
and a full timeline event log.

Design: This service is additive on top of the existing alert_state
collection. Both can coexist; alert_state continues to gate duplicate alert
suppression while incidents provides the rich lifecycle layer.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

INCIDENT_ID_PREFIX = os.getenv("INCIDENT_ID_PREFIX", "INC")


def _generate_incident_id(mongo_client) -> str:
    """Generate sequential incident ID: INC-YYYY-NNN."""
    db = mongo_client["ServerAutomation"]
    year = datetime.now(timezone.utc).year

    # Atomic counter using MongoDB findAndModify pattern
    counter_doc = db["incident_counter"].find_one_and_update(
        {"_id": f"incident_{year}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    seq = counter_doc.get("seq", 1)
    return f"{INCIDENT_ID_PREFIX}-{year}-{seq:03d}"


def _compute_mttd(service_id: str, incident_start: datetime, mongo_client) -> Optional[float]:
    """
    Compute Mean Time To Detect.
    MTTD = incident_detected_at - last_successful_check_before_incident.
    """
    try:
        db = mongo_client["ServerAutomation"]
        last_ok = db["monitoring_history"].find_one(
            {
                "service_id": service_id,
                "status": "success",
                "timestamp": {"$lt": incident_start}
            },
            sort=[("timestamp", -1)]
        )
        if last_ok and last_ok.get("timestamp"):
            last_ok_ts = last_ok["timestamp"]
            if last_ok_ts.tzinfo is None:
                last_ok_ts = last_ok_ts.replace(tzinfo=timezone.utc)
            mttd = (incident_start - last_ok_ts).total_seconds()
            return round(mttd, 1)
        # If no prior success found, fallback: monitoring interval ≈ 5 minutes
        return 300.0
    except Exception as e:
        logging.warning(f"[IncidentService] MTTD computation failed: {e}")
        return None


def open_incident(
    service_id: str,
    service_name: str,
    trigger_alert_type: str,
    failure_reason: Optional[str],
    severity: str,
    mongo_client,
) -> str:
    """
    Create a new incident record.

    Returns: incident_id string (e.g. 'INC-2026-001')
    Raises: Does NOT raise — logs errors and returns empty string on failure.
    """
    try:
        db = mongo_client["ServerAutomation"]
        incidents_col = db["incidents"]

        # Prevent duplicate open incidents for the same service
        existing = incidents_col.find_one({
            "service_id": service_id,
            "status": {"$in": ["open", "acknowledged"]}
        })
        if existing:
            logging.info(
                f"[IncidentService] Active incident {existing['incident_id']} already exists for {service_id}. Skipping open."
            )
            return existing["incident_id"]

        now = datetime.now(timezone.utc)
        incident_id = _generate_incident_id(mongo_client)
        mttd = _compute_mttd(service_id, now, mongo_client)

        # Generate SRE-level root cause diagnostics using Groq AI
        ai_analysis = ""
        if os.getenv("GROQ_API_KEY"):
            try:
                from services.groq_service import generate_groq_analysis
                ai_analysis = generate_groq_analysis(
                    service_id=service_id,
                    service_name=service_name,
                    failure_reason=failure_reason or f"Triggered by {trigger_alert_type}",
                    mongo_client=mongo_client
                )
            except Exception as ai_err:
                logging.error(f"[IncidentService] Groq AI analysis failed: {ai_err}")
                ai_analysis = f"Failed to generate AI analysis: {ai_err}"

        incident_doc = {
            "incident_id": incident_id,
            "service_id": service_id,
            "service_name": service_name,
            "started_at": now,
            "detected_at": now,
            "resolved_at": None,
            "acknowledged_at": None,
            "acknowledged_by": None,
            "status": "open",
            "severity": severity,
            "trigger_alert_type": trigger_alert_type,
            "failure_reason": failure_reason,
            "mttd_seconds": mttd,
            "mttr_seconds": None,
            "ai_analysis": ai_analysis,
            "timeline": [
                {
                    "timestamp": now,
                    "event": "INCIDENT_OPENED",
                    "note": failure_reason or f"Triggered by {trigger_alert_type}"
                }
            ]
        }

        incidents_col.insert_one(incident_doc)
        # Ensure indexes exist
        _ensure_indexes(mongo_client)
        logging.info(f"[IncidentService] Incident {incident_id} opened for {service_id}")
        return incident_id

    except Exception as e:
        logging.error(f"[IncidentService] Failed to open incident for {service_id}: {e}")
        return ""


def acknowledge_incident(incident_id: str, acknowledged_by: str, mongo_client) -> bool:
    """
    Mark an open incident as acknowledged.
    Returns True if updated, False if not found or already resolved.
    """
    try:
        db = mongo_client["ServerAutomation"]
        now = datetime.now(timezone.utc)

        result = db["incidents"].update_one(
            {"incident_id": incident_id, "status": "open"},
            {
                "$set": {
                    "status": "acknowledged",
                    "acknowledged_at": now,
                    "acknowledged_by": acknowledged_by or "dashboard"
                },
                "$push": {
                    "timeline": {
                        "timestamp": now,
                        "event": "ACKNOWLEDGED",
                        "note": f"Acknowledged by {acknowledged_by or 'dashboard'}"
                    }
                }
            }
        )
        if result.modified_count > 0:
            logging.info(f"[IncidentService] Incident {incident_id} acknowledged by {acknowledged_by}")
            return True
        logging.warning(f"[IncidentService] Incident {incident_id} not found or not in open state")
        return False

    except Exception as e:
        logging.error(f"[IncidentService] Failed to acknowledge incident {incident_id}: {e}")
        return False


def resolve_incident(service_id: str, mongo_client) -> Optional[dict]:
    """
    Resolve the active incident for a service and compute MTTR.

    Returns the resolved incident document dict, or None if no active incident.
    """
    try:
        db = mongo_client["ServerAutomation"]
        now = datetime.now(timezone.utc)

        active = db["incidents"].find_one({
            "service_id": service_id,
            "status": {"$in": ["open", "acknowledged"]}
        })

        if not active:
            logging.info(f"[IncidentService] No active incident to resolve for {service_id}")
            return None

        started_at = active.get("started_at")
        if started_at and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        mttr = round((now - started_at).total_seconds(), 1) if started_at else None

        db["incidents"].update_one(
            {"_id": active["_id"]},
            {
                "$set": {
                    "status": "resolved",
                    "resolved_at": now,
                    "mttr_seconds": mttr
                },
                "$push": {
                    "timeline": {
                        "timestamp": now,
                        "event": "RESOLVED",
                        "note": f"Service recovered. MTTR: {_fmt_duration(mttr)}"
                    }
                }
            }
        )

        logging.info(
            f"[IncidentService] Incident {active['incident_id']} resolved for {service_id}. "
            f"MTTR={_fmt_duration(mttr)}"
        )
        active["resolved_at"] = now.isoformat()
        active["mttr_seconds"] = mttr
        active["status"] = "resolved"
        return active

    except Exception as e:
        logging.error(f"[IncidentService] Failed to resolve incident for {service_id}: {e}")
        return None


def get_active_incident(service_id: str, mongo_client) -> Optional[dict]:
    """Return the currently open/acknowledged incident for a service, or None."""
    try:
        db = mongo_client["ServerAutomation"]
        doc = db["incidents"].find_one(
            {"service_id": service_id, "status": {"$in": ["open", "acknowledged"]}},
            sort=[("started_at", -1)]
        )
        if doc:
            return _serialize_incident(doc)
        return None
    except Exception as e:
        logging.error(f"[IncidentService] get_active_incident error: {e}")
        return None


def get_incident_history(service_id: Optional[str], status: Optional[str], limit: int, mongo_client) -> list:
    """Return incident history with optional filters."""
    try:
        db = mongo_client["ServerAutomation"]
        query = {}
        if service_id:
            query["service_id"] = service_id
        if status:
            query["status"] = status

        docs = list(
            db["incidents"]
            .find(query)
            .sort("started_at", -1)
            .limit(limit)
        )
        return [_serialize_incident(d) for d in docs]
    except Exception as e:
        logging.error(f"[IncidentService] get_incident_history error: {e}")
        return []


def get_incident_by_id(incident_id: str, mongo_client) -> Optional[dict]:
    """Return a single incident by its incident_id string."""
    try:
        db = mongo_client["ServerAutomation"]
        doc = db["incidents"].find_one({"incident_id": incident_id})
        return _serialize_incident(doc) if doc else None
    except Exception as e:
        logging.error(f"[IncidentService] get_incident_by_id error: {e}")
        return None


def get_incident_metrics(days: int, mongo_client) -> dict:
    """
    Compute MTTD, MTTR, and incident counts for the given time window.
    """
    try:
        db = mongo_client["ServerAutomation"]
        col = db["incidents"]
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        pipeline = [
            {"$match": {"started_at": {"$gte": cutoff}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "open": {"$sum": {"$cond": [{"$eq": ["$status", "open"]}, 1, 0]}},
                "acknowledged": {"$sum": {"$cond": [{"$eq": ["$status", "acknowledged"]}, 1, 0]}},
                "resolved": {"$sum": {"$cond": [{"$eq": ["$status", "resolved"]}, 1, 0]}},
                "avg_mttd": {"$avg": "$mttd_seconds"},
                "avg_mttr": {"$avg": "$mttr_seconds"},
            }}
        ]
        agg = list(col.aggregate(pipeline))

        # Per-service breakdown
        svc_pipeline = [
            {"$match": {"started_at": {"$gte": cutoff}}},
            {"$group": {"_id": "$service_name", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        svc_agg = list(col.aggregate(svc_pipeline))
        incidents_by_service = {r["_id"]: r["count"] for r in svc_agg if r["_id"]}

        # Per-day breakdown (last `days` days)
        day_pipeline = [
            {"$match": {"started_at": {"$gte": cutoff}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$started_at"}},
                "count": {"$sum": 1}
            }},
            {"$sort": {"_id": 1}}
        ]
        day_agg = list(col.aggregate(day_pipeline))
        incidents_by_day = {r["_id"]: r["count"] for r in day_agg}

        if agg:
            m = agg[0]
            return {
                "days": days,
                "total_incidents": m.get("total", 0),
                "open_incidents": m.get("open", 0),
                "acknowledged_incidents": m.get("acknowledged", 0),
                "resolved_incidents": m.get("resolved", 0),
                "avg_mttd_seconds": round(m["avg_mttd"], 1) if m.get("avg_mttd") else None,
                "avg_mttr_seconds": round(m["avg_mttr"], 1) if m.get("avg_mttr") else None,
                "incidents_by_service": incidents_by_service,
                "incidents_by_day": incidents_by_day,
            }

        return {
            "days": days,
            "total_incidents": 0,
            "open_incidents": 0,
            "acknowledged_incidents": 0,
            "resolved_incidents": 0,
            "avg_mttd_seconds": None,
            "avg_mttr_seconds": None,
            "incidents_by_service": {},
            "incidents_by_day": {},
        }

    except Exception as e:
        logging.error(f"[IncidentService] get_incident_metrics error: {e}")
        return {
            "days": days,
            "total_incidents": 0,
            "open_incidents": 0,
            "acknowledged_incidents": 0,
            "resolved_incidents": 0,
            "avg_mttd_seconds": None,
            "avg_mttr_seconds": None,
            "incidents_by_service": {},
            "incidents_by_day": {},
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds // 60:.0f}m {seconds % 60:.0f}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h:.0f}h {m:.0f}m"


def _serialize_incident(doc: dict) -> dict:
    """Convert a MongoDB incident document to a JSON-serializable dict."""
    if doc is None:
        return {}

    def _iso(v):
        if isinstance(v, datetime):
            return v.isoformat()
        return v

    timeline = []
    for event in doc.get("timeline", []):
        timeline.append({
            "timestamp": _iso(event.get("timestamp")),
            "event": event.get("event", ""),
            "note": event.get("note")
        })

    return {
        "incident_id": doc.get("incident_id", ""),
        "service_id": doc.get("service_id", ""),
        "service_name": doc.get("service_name", ""),
        "started_at": _iso(doc.get("started_at")),
        "detected_at": _iso(doc.get("detected_at")),
        "resolved_at": _iso(doc.get("resolved_at")),
        "acknowledged_at": _iso(doc.get("acknowledged_at")),
        "acknowledged_by": doc.get("acknowledged_by"),
        "status": doc.get("status", "open"),
        "severity": doc.get("severity", "critical"),
        "trigger_alert_type": doc.get("trigger_alert_type", ""),
        "failure_reason": doc.get("failure_reason"),
        "mttd_seconds": doc.get("mttd_seconds"),
        "mttr_seconds": doc.get("mttr_seconds"),
        "ai_analysis": doc.get("ai_analysis", ""),
        "timeline": timeline,
    }


def _ensure_indexes(mongo_client):
    """Create indexes on incidents collection (idempotent)."""
    try:
        from pymongo import ASCENDING, DESCENDING
        db = mongo_client["ServerAutomation"]
        col = db["incidents"]
        col.create_index([("service_id", ASCENDING), ("started_at", DESCENDING)], name="svc_started_idx")
        col.create_index([("status", ASCENDING)], name="status_idx")
        col.create_index([("incident_id", ASCENDING)], name="incident_id_idx", unique=True)
    except Exception as e:
        logging.warning(f"[IncidentService] Index creation warning: {e}")
