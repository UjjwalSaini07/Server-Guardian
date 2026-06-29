import time
import httpx
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from config import SERVICES_CONFIG
from services.analytics_service import parse_health_json
from services.health_check_service import evaluate_health_score
from services.root_cause_engine import RootCauseEngine
from services.timeline_service import log_event
from services.retry_service import should_retry, calculate_backoff, log_retry
from services.reliability_engine import calculate_reliability
from services.monitoring_service import is_within_allowed_hours

logger = logging.getLogger(__name__)

MONITOR_VERSION = "2.0"
CONCURRENCY_LIMIT = int(os.getenv("MAX_CONCURRENT_PINGS", "20"))
DEFAULT_TIMEOUT = float(os.getenv("PING_TIMEOUT_SECONDS", "60.0"))

async def monitor_service(service: dict, client: httpx.AsyncClient, mongo_client) -> Dict[str, Any]:
    """
    Monitor a single service asynchronously, implementing retry-with-backoff,
    Root Cause Analysis (RCA), timeline logging, and reliability updates.
    """
    name = service["name"]
    url = service["url"]
    service_id = service.get("service_id", "quillix_api")
    db_name = service.get("db_name", "ServerAutomation")
    col_name = service.get("collection_name", "health_logs")
    parse_analytics = service.get("parse_analytics", False)

    # 0. Check for empty or missing URL configuration
    if not url:
        logger.error(f"[AsyncMonitor] Service {name} is enabled but has no URL configured!")
        now = datetime.now(timezone.utc)
        status_data = {
            "name": name,
            "type": "pinger",
            "url": "",
            "status": "ERROR",
            "status_code": None,
            "latency_ms": 0,
            "last_checked": now,
            "db_ok": False,
            "redis_ok": False,
            "error": "Configuration Error: Service URL is empty or not defined. Please configure it in your environment variables or GitHub repository secrets.",
            "retry_count": 0,
            "retry_status": "none",
            "final_failure": True,
            "recovery_attempt": False
        }
        history_data = {
            "service_id": service_id,
            "service_name": name,
            "timestamp": now,
            "status": "failure",
            "status_code": None,
            "latency_ms": 0,
            "failure_reason": status_data["error"],
            "monitor_version": MONITOR_VERSION,
            "rca": {
                "failure_type": "UNKNOWN_FAILURE",
                "severity": "critical",
                "confidence_score": 100,
                "recommended_action": "Add the NEXORA_SERVER_URL variable to your local .env file or GitHub repository secrets.",
                "technical_summary": "Service is enabled but the connection URL is empty."
            }
        }
        try:
            sa_db = mongo_client["ServerAutomation"]
            sa_db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
            sa_db["monitoring_history"].insert_one(history_data)
        except Exception as e:
            logger.error(f"[AsyncMonitor] Failed to write missing URL status for {name}: {e}")
        return status_data

    # 1. Active hours schedule gate
    if not is_within_allowed_hours(service.get("allowed_hours_ist"), service.get("allowed_days")):
        logger.info(f"[AsyncMonitor] Skipping ping for {name} (outside active hours/days).")
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
            "redis_ok": True,
            "retry_count": 0,
            "retry_status": "none",
            "final_failure": False,
            "recovery_attempt": False
        }
        
        history_data = {
            "service_id": service_id,
            "service_name": name,
            "timestamp": now,
            "status": "success",
            "latency_ms": 0,
            "failure_reason": "Skipped (Outside active schedule)",
            "monitor_version": MONITOR_VERSION
        }
        
        try:
            db = mongo_client["ServerAutomation"]
            db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
            db["monitoring_history"].insert_one(history_data)
        except Exception as e:
            logger.error(f"[AsyncMonitor] Failed to write skipped status for {name}: {e}")
        return status_data

    # ── Ping loop with backoff retries ────────────────────────────────────────
    max_attempts = service.get("retry_attempts", 3)
    attempt = 0
    response = None
    last_exception = None
    latency = 0.0
    status_code = None
    response_text = ""
    
    # Track states for UI metrics
    retry_status = "none"
    recovery_attempt = False
    final_failure = False

    while attempt <= max_attempts:
        if attempt > 0:
            retry_status = "retrying"
            backoff_sleep = calculate_backoff(attempt, service)
            logger.info(f"[AsyncMonitor] Retry {attempt}/{max_attempts} for {name} in {backoff_sleep:.2f}s...")
            await asyncio.sleep(backoff_sleep)

        attempt_start_time = time.time()
        try:
            response = await client.get(url, timeout=DEFAULT_TIMEOUT)
            latency = (time.time() - attempt_start_time) * 1000
            status_code = response.status_code
            response_text = response.text
            last_exception = None
            
            # If healthy or a persistent/client error (like 404), do not retry
            is_transient = should_retry(status_code, None, service)
            if not is_transient:
                if attempt > 0:
                    recovery_attempt = (status_code == 200)
                break
            
            # If it is transient, log the retry attempt
            if attempt < max_attempts:
                log_retry(service_id, attempt + 1, max_attempts, "failed", latency, f"HTTP {status_code}", True, mongo_client)

        except (httpx.RequestError, asyncio.TimeoutError) as e:
            latency = (time.time() - attempt_start_time) * 1000
            last_exception = e
            response = None
            status_code = None
            response_text = ""
            
            # Log connection/timeout retry
            if attempt < max_attempts:
                log_retry(service_id, attempt + 1, max_attempts, "failed", latency, str(e), True, mongo_client)
        
        attempt += 1

    now = datetime.now(timezone.utc)
    expire_time = now + timedelta(hours=service.get("log_expiry_hours", 1))

    # Determine final outcome
    is_success = (response is not None and status_code == 200)
    
    # Set final retry status fields
    if not is_success:
        final_failure = True
        retry_status = "failed" if attempt > 1 else "none"
    else:
        retry_status = "recovered_after_retry" if attempt > 1 else "none"
        recovery_attempt = (attempt > 1)

    parsed_json = None
    analytics = {}
    if response:
        try:
            parsed_json = response.json()
            if parse_analytics:
                analytics = parse_health_json(name, parsed_json)
        except Exception:
            pass

    # ── 1. Root Cause Analysis (RCA) ──────────────────────────────────────────
    rca_data = None
    if not is_success:
        rca_data = RootCauseEngine.diagnose(
            service_config=service,
            response_json=parsed_json,
            status_code=status_code,
            latency_ms=latency,
            error_exception=last_exception
        )

    # ── 2. Incident Opening and Resolutions ───────────────────────────────────
    incident_opened = False
    sa_db = mongo_client["ServerAutomation"]
    state_col = sa_db["alert_state"]
    state_doc = state_col.find_one({"service_id": service_id}) or {}
    was_down = state_doc.get("active_incident", False)

    if not is_success:
        failure_reason = rca_data["technical_summary"] if rca_data else (str(last_exception) if last_exception else f"HTTP {status_code}")
        
        # Open formal incident via incident_service
        if not was_down:
            from services.incident_service import open_incident
            open_incident(
                service_id=service_id,
                service_name=name,
                trigger_alert_type="SERVICE_DOWN",
                failure_reason=failure_reason,
                severity=rca_data["severity"] if rca_data else "critical",
                mongo_client=mongo_client
            )
            incident_opened = True
            
            # Log timeline event
            log_event(
                service_id=service_id,
                event_type="SERVICE_DOWN",
                severity="critical",
                description=f"Service {name} went down: {failure_reason}",
                mongo_client=mongo_client,
                root_cause=rca_data
            )
    else:
        if was_down:
            from services.incident_service import resolve_incident
            resolve_incident(service_id=service_id, mongo_client=mongo_client)
            
            # Log timeline event
            log_event(
                service_id=service_id,
                event_type="HEALTHY",
                severity="success",
                description=f"Service {name} recovered successfully.",
                mongo_client=mongo_client
            )
        elif recovery_attempt:
            log_event(
                service_id=service_id,
                event_type="RECOVERED_AFTER_RETRY",
                severity="success",
                description=f"Service {name} recovered after transient failure retries.",
                mongo_client=mongo_client
            )

    # ── 3. Populate metrics & Save docs ──────────────────────────────────────
    status = "SUCCESS" if is_success else ("ERROR" if last_exception else f"FAILED: {status_code}")
    
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

    # Write detailed log
    db = mongo_client[db_name]
    db[col_name].insert_one(log_doc)

    health_score = 100
    health_detail = {}
    if is_success:
        health_score, health_detail = evaluate_health_score(
            response_json=parsed_json,
            status_code=status_code,
            latency_ms=latency,
            service_config=service
        )
    else:
        health_score = 0
        health_detail = {"http_ok": False, "db_ok": False, "cache_ok": False}

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
        "details": analytics.get("details", {}) if parse_analytics else {},
        "error": rca_data["technical_summary"] if rca_data else (str(last_exception) if last_exception else None),
        "rca": rca_data,
        
        # Retry statistics integration
        "retry_count": attempt - 1 if attempt > 0 else 0,
        "retry_status": retry_status,
        "final_failure": final_failure,
        "recovery_attempt": recovery_attempt
    }

    history_data = {
        "service_id": service_id,
        "service_name": name,
        "timestamp": now,
        "status": "success" if is_success else "failure",
        "status_code": status_code,
        "latency_ms": latency,
        "failure_reason": status_data["error"],
        "health_score": health_score,
        "health_detail": health_detail,
        "response_size": len(response_text) if response_text else None,
        "response_time_ms": latency,
        "api_status": is_success,
        "database_status": health_detail.get("db_ok"),
        "cache_status": health_detail.get("cache_ok"),
        "error_type": type(last_exception).__name__ if last_exception else None,
        "monitor_version": MONITOR_VERSION,
        "rca": rca_data,
        
        # Retry fields inside history
        "retry_attempts": attempt - 1 if attempt > 0 else 0,
        "final_failure": final_failure
    }

    sa_db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
    sa_db["monitoring_history"].insert_one(history_data)

    # ── 4. Recalculate AI Reliability metrics ────────────────────────────────
    calculate_reliability(service_id, name, mongo_client)

    # Dispatch alerts
    try:
        from services.alert_service import evaluate_ping_result
        evaluate_ping_result(service, status_data, mongo_client)
    except Exception as alert_err:
        logger.error(f"[AsyncMonitor] Alert evaluation failed for {name}: {alert_err}")

    return status_data

async def monitor_all_services(mongo_client) -> List[Dict[str, Any]]:
    """
    Asynchronously monitors all configured pinger services concurrently under concurrency limits.
    """
    logger.info(f"[AsyncMonitor] Starting asynchronous monitoring run (concurrency={CONCURRENCY_LIMIT})...")
    
    # Initialize connection pooling limits
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    
    async with httpx.AsyncClient(limits=limits, verify=False) as client:
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        
        async def sem_monitor(service):
            async with semaphore:
                try:
                    return await monitor_service(service, client, mongo_client)
                except asyncio.CancelledError:
                    logger.warning(f"[AsyncMonitor] Monitor task cancelled for {service['name']}")
                    raise
                except Exception as e:
                    logger.error(f"[AsyncMonitor] Unexpected error monitoring {service['name']}: {e}")
                    return {"name": service["name"], "status": "ERROR", "error": str(e)}

        tasks = []
        for s in SERVICES_CONFIG:
            if s.get("enabled", True) and s["type"] == "pinger":
                tasks.append(sem_monitor(s))
                
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out exceptions
        valid_results = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"[AsyncMonitor] Gather encountered unhandled exception task: {r}")
            else:
                valid_results.append(r)
                
        logger.info(f"[AsyncMonitor] Asynchronous monitoring run completed. Processed {len(valid_results)} tasks.")
        return valid_results
