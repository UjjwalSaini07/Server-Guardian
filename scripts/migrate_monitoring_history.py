"""
scripts/migrate_monitoring_history.py

One-time migration: backfill legacy monitoring_history records written
by the old monitoring_service.py (before Phase 1) with the fields
required by the analytics engine.

Legacy schema:
  { service, timestamp, status, latency, error }

Target schema (new optional fields added):
  { service_id, service_name, timestamp, status (normalised),
    latency_ms, status_code, failure_reason,
    health_score, response_size, response_time_ms,
    api_status, database_status, cache_status,
    error_type, monitor_version }

Safety guarantees:
  - Only touches documents where service_id is missing (legacy).
  - Never overwrites documents that already have service_id.
  - Idempotent: safe to run multiple times.
  - Does not delete any document or field.
"""

import os
import sys
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from pymongo import MongoClient
from config import SERVICES_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("MigrationScript")

MONITOR_VERSION = "1.0"

# Build lookup: service display name → service config
NAME_TO_CONFIG = {s["name"]: s for s in SERVICES_CONFIG if s.get("service_id")}


def normalise_status(raw_status: str) -> str:
    """
    Convert legacy uppercase status strings to the normalised lowercase form.
    SUCCESS / SKIPPED  →  success
    anything else      →  failure
    """
    if isinstance(raw_status, str):
        s = raw_status.strip().upper()
        if s in ("SUCCESS", "SKIPPED"):
            return "success"
    return "failure"


def derive_status_code(raw_status: str):
    """
    Extract numeric status code from strings like 'FAILED: 500'.
    Returns int or None.
    """
    if isinstance(raw_status, str) and raw_status.startswith("FAILED:"):
        try:
            return int(raw_status.split(":")[1].strip())
        except (IndexError, ValueError):
            pass
    if isinstance(raw_status, str) and raw_status.strip().upper() == "SUCCESS":
        return 200
    return None


def compute_health_score(normalised_status: str) -> int:
    return 100 if normalised_status == "success" else 0


def migrate(mongo_client):
    db = mongo_client["ServerAutomation"]
    col = db["monitoring_history"]

    # Only target legacy documents (no service_id field)
    legacy_filter = {"service_id": {"$exists": False}}
    total_legacy = col.count_documents(legacy_filter)
    log.info("Legacy documents found: %d", total_legacy)

    if total_legacy == 0:
        log.info("No legacy documents to migrate. Exiting.")
        return 0

    migrated = 0
    skipped  = 0
    errors   = 0

    cursor = col.find(legacy_filter)
    for doc in cursor:
        doc_id       = doc["_id"]
        service_name = doc.get("service", "")
        raw_status   = doc.get("status", "")
        raw_latency  = doc.get("latency")

        # Look up config by name
        cfg = NAME_TO_CONFIG.get(service_name)
        if not cfg:
            log.warning("Unknown service name '%s' – skipping _id=%s", service_name, doc_id)
            skipped += 1
            continue

        service_id      = cfg["service_id"]
        norm_status     = normalise_status(raw_status)
        status_code     = derive_status_code(raw_status)
        is_success      = norm_status == "success"
        health_score    = compute_health_score(norm_status)
        latency_ms      = float(raw_latency) if isinstance(raw_latency, (int, float)) else None
        failure_reason  = None if is_success else f"Legacy: {raw_status}"
        error_type      = doc.get("error") or None

        update_fields = {
            # Core normalised fields
            "service_id":      service_id,
            "service_name":    service_name,
            "status":          norm_status,
            "status_code":     status_code,
            "latency_ms":      latency_ms,
            "failure_reason":  failure_reason,
            # Extended analytics fields
            "health_score":      health_score,
            "response_size":     None,
            "response_time_ms":  latency_ms,
            "api_status":        is_success,
            "database_status":   None,
            "cache_status":      None,
            "error_type":        error_type,
            "monitor_version":   MONITOR_VERSION,
            "_migrated_at":      datetime.now(timezone.utc),
        }

        try:
            result = col.update_one(
                {"_id": doc_id, "service_id": {"$exists": False}},  # extra guard
                {"$set": update_fields}
            )
            if result.modified_count == 1:
                migrated += 1
                log.info("Migrated: %s | %s | status=%s → %s | latency=%.1fms",
                         service_id, doc.get("timestamp", ""), raw_status,
                         norm_status, latency_ms or 0)
            else:
                log.warning("No-op (already migrated?): _id=%s", doc_id)
                skipped += 1
        except Exception as exc:
            log.error("Error migrating _id=%s: %s", doc_id, exc)
            errors += 1

    log.info("=" * 60)
    log.info("Migration complete: migrated=%d skipped=%d errors=%d",
             migrated, skipped, errors)
    return migrated


if __name__ == "__main__":
    from config import MONGO_URI
    client = MongoClient(MONGO_URI)
    try:
        count = migrate(client)
        if count > 0:
            # Verify
            db = client["ServerAutomation"]
            remaining_legacy = db["monitoring_history"].count_documents(
                {"service_id": {"$exists": False}}
            )
            log.info("Verification: remaining legacy docs = %d", remaining_legacy)
    finally:
        client.close()
        log.info("MongoDB connection closed.")
