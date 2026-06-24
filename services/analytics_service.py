import logging

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
        logging.error(f"[AnalyticsService] Error parsing custom metrics for {service_name}: {e}")
        
    return parsed
