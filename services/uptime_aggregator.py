import logging
from datetime import datetime, timezone
from config import SERVICES_CONFIG
from services.uptime_service import calculate_uptime

def aggregate_metrics(mongo_client):
    """
    Recalculate uptime metrics for all configured services and cache them in MongoDB.
    """
    logging.info("[UptimeAggregator] Starting uptime aggregation...")
    try:
        db = mongo_client["ServerAutomation"]
        col = db["uptime_metrics"]
        
        for s in SERVICES_CONFIG:
            service_id = s.get("service_id")
            if not service_id:
                continue
                
            uptime_24h, _, _ = calculate_uptime(service_id, mongo_client, days=1)
            uptime_7d, _, _ = calculate_uptime(service_id, mongo_client, days=7)
            uptime_30d, _, _ = calculate_uptime(service_id, mongo_client, days=30)
            uptime_all_time, success_count, failed_count = calculate_uptime(service_id, mongo_client, days=None)
            
            from services.uptime_service import get_reliability_rating, get_trend_indicator, detect_consecutive_outages
            rating = get_reliability_rating(uptime_30d)
            trend = get_trend_indicator(service_id, mongo_client)
            outage = detect_consecutive_outages(service_id, mongo_client)
            
            metric_doc = {
                "service_id": service_id,
                "service_name": s["name"],
                "uptime_24h": uptime_24h,
                "uptime_7d": uptime_7d,
                "uptime_30d": uptime_30d,
                "uptime_all_time": uptime_all_time,
                "successful_checks": success_count,
                "failed_checks": failed_count,
                "reliability_rating": rating,
                "trend_indicator": trend,
                "consecutive_outages": outage,
                "last_updated": datetime.now(timezone.utc)
            }
            
            col.update_one(
                {"service_id": service_id},
                {"$set": metric_doc},
                upsert=True
            )
            logging.info(f"[UptimeAggregator] Aggregated uptime metrics for {service_id}: all-time={uptime_all_time}%")
            
        logging.info("[UptimeAggregator] Uptime aggregation completed successfully.")
    except Exception as e:
        logging.error(f"[UptimeAggregator] Error during uptime aggregation: {e}")

def get_platform_overview(mongo_client):
    """
    Calculate and return overall platform diagnostics.
    """
    try:
        db = mongo_client["ServerAutomation"]
        col = db["uptime_metrics"]
        
        metrics = list(col.find())
        if not metrics:
            return {
                "overall_platform_uptime": 100.0,
                "best_service": "N/A",
                "worst_service": "N/A",
                "total_checks": 0
            }
            
        total_success = 0
        total_failed = 0
        
        best_service = None
        best_uptime = -1.0
        worst_service = None
        worst_uptime = 101.0
        
        for m in metrics:
            success = m.get("successful_checks", 0)
            failed = m.get("failed_checks", 0)
            total_success += success
            total_failed += failed
            
            uptime = m.get("uptime_all_time", 100.0)
            name = m.get("service_name", m["service_id"])
            
            if uptime > best_uptime:
                best_uptime = uptime
                best_service = name
                
            if uptime < worst_uptime:
                worst_uptime = uptime
                worst_service = name
                
        total_checks = total_success + total_failed
        overall_uptime = (total_success / total_checks * 100.0) if total_checks > 0 else 100.0
        
        return {
            "overall_platform_uptime": round(overall_uptime, 2),
            "best_service": best_service or "N/A",
            "worst_service": worst_service or "N/A",
            "total_checks": total_checks
        }
    except Exception as e:
        logging.error(f"[UptimeAggregator] Error fetching platform overview: {e}")
        return {
            "overall_platform_uptime": 100.0,
            "best_service": "N/A",
            "worst_service": "N/A",
            "total_checks": 0
        }
