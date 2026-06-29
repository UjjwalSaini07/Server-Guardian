import random
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pymongo import DESCENDING

logger = logging.getLogger(__name__)

def should_retry(
    status_code: Optional[int],
    error_exception: Optional[Exception],
    service_config: dict
) -> bool:
    """
    Distinguish between transient and persistent failures.
    Do NOT retry 401, 403, 404 unless explicitly configured.
    """
    # Check if retry is explicitly disabled
    if service_config.get("retry_attempts", 3) <= 0:
        return False

    # Connection / Socket / Timeout exceptions are always transient
    if error_exception:
        return True

    if status_code:
        # Non-retryable client errors by default
        explicitly_allowed = service_config.get("retry_on_status_codes", [])
        if status_code in explicitly_allowed:
            return True
            
        if status_code in (401, 403, 404):
            return False
            
        # Client errors (400-499) are usually persistent; Server errors (500-599) are transient
        if 500 <= status_code < 600:
            return True
        if status_code == 408 or status_code == 429: # Request Timeout, Too Many Requests
            return True
            
    return False

def calculate_backoff(attempt: int, service_config: dict) -> float:
    """
    Calculate backoff interval using exponential backoff with full jitter.
    """
    base_interval = service_config.get("retry_interval", 2.0)
    backoff_factor = service_config.get("backoff_factor", 2.0)
    jitter = service_config.get("jitter", True)
    
    # Calculate exponential backoff: base * (factor ^ (attempt - 1))
    temp = base_interval * (backoff_factor ** (attempt - 1))
    
    if jitter:
        # Full jitter logic (random value between 0 and temp)
        sleep_time = random.uniform(0.1, temp)
    else:
        sleep_time = temp
        
    return sleep_time

def log_retry(
    service_id: str,
    attempt: int,
    max_attempts: int,
    status: str,
    latency_ms: float,
    error: Optional[str],
    is_transient: bool,
    mongo_client
) -> Dict[str, Any]:
    """
    Log an entry in the retry history collection.
    """
    try:
        db = mongo_client["ServerAutomation"]
        retry_col = db["retry_history"]
        
        now = datetime.now(timezone.utc)
        retry_doc = {
            "timestamp": now,
            "service_id": service_id,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "status": status, # "success" or "failed"
            "latency_ms": latency_ms,
            "error": error,
            "is_transient": is_transient
        }
        
        retry_col.insert_one(retry_doc)
        retry_col.create_index([("service_id", 1), ("timestamp", -1)])
        
        logger.info(f"[RetryService] Logged retry attempt {attempt}/{max_attempts} for service {service_id}")
        return retry_doc
    except Exception as e:
        logger.error(f"[RetryService] Failed to log retry: {e}")
        return {}

def get_retry_history(service_id: Optional[str], limit: int, mongo_client) -> List[Dict[str, Any]]:
    """
    Fetch historical retry records.
    """
    try:
        db = mongo_client["ServerAutomation"]
        retry_col = db["retry_history"]
        
        query = {}
        if service_id:
            query["service_id"] = service_id
            
        records = list(
            retry_col.find(query)
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )
        
        return [{
            "timestamp": r["timestamp"].isoformat(),
            "service_id": r["service_id"],
            "attempt": r["attempt"],
            "max_attempts": r["max_attempts"],
            "status": r["status"],
            "latency_ms": r["latency_ms"],
            "error": r["error"],
            "is_transient": r["is_transient"]
        } for r in records]
    except Exception as e:
        logger.error(f"[RetryService] Failed to fetch retry history: {e}")
        return []

def get_retry_stats(mongo_client) -> Dict[str, Any]:
    """
    Calculate and return aggregate retry stats.
    """
    try:
        db = mongo_client["ServerAutomation"]
        retry_col = db["retry_history"]
        
        total_retries = retry_col.count_documents({})
        success_retries = retry_col.count_documents({"status": "success"})
        failed_retries = total_retries - success_retries
        
        # Calculate success rate
        success_rate = (success_retries / total_retries * 100.0) if total_retries > 0 else 100.0
        
        # Most retried services
        pipeline = [
            {"$group": {"_id": "$service_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]
        top_retried = list(retry_col.aggregate(pipeline))
        retries_by_service = {r["_id"]: r["count"] for r in top_retried}
        
        return {
            "total_retries": total_retries,
            "successful_retries": success_retries,
            "failed_retries": failed_retries,
            "success_rate_pct": round(success_rate, 2),
            "retries_by_service": retries_by_service
        }
    except Exception as e:
        logger.error(f"[RetryService] Failed to compute retry stats: {e}")
        return {
            "total_retries": 0,
            "successful_retries": 0,
            "failed_retries": 0,
            "success_rate_pct": 100.0,
            "retries_by_service": {}
        }
