import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from servers.stock_scraper_service import scrape_stocks

def run_scraper(service, mongo_client):
    """Run the stock scraping job, update latest_status and monitoring_history in MongoDB."""
    from services.monitoring_service import is_within_allowed_hours
    
    name = service["name"]
    db_name = "ServerAutomation"
    
    if not is_within_allowed_hours(service["allowed_hours_ist"], service["allowed_days"]):
        logging.info(f"[ScraperService] Skipping stock scraper (outside active hours/days).")
        now = datetime.now(timezone.utc)
        
        status_data = {
            "name": name,
            "type": "scraper",
            "status": "SKIPPED",
            "message": "Outside scraping hours (Mon-Fri 9:30-16:00 IST)",
            "last_checked": now,
            "details": {"status": "skipped", "message": "outside allowed hours"}
        }
        
        history_data = {
            "service_id": service.get("service_id", "stock_scraper"),
            "service_name": name,
            "timestamp": now,
            "status": "success",
            "latency_ms": 0,
            "failure_reason": "Skipped (Outside active schedule)"
        }
        
        # Save to database
        try:
            db = mongo_client[db_name]
            db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
            db["monitoring_history"].insert_one(history_data)
        except Exception as e:
            logging.error(f"[ScraperService] Failed to write skipped scraper status to database: {e}")
            
        return status_data

    start_time = datetime.now(timezone.utc)
    # Set status to RUNNING in latest_status so the dashboard shows it is active
    try:
        db = mongo_client[db_name]
        db["latest_status"].update_one(
            {"name": name},
            {"$set": {
                "name": name,
                "type": "scraper",
                "status": "RUNNING",
                "last_checked": start_time,
                "details": {"status": "running"}
            }},
            upsert=True
        )
    except Exception as e:
        logging.error(f"[ScraperService] Failed to update running scraper status in database: {e}")

    try:
        # Run scraper service
        result = scrape_stocks(mongo_client)
        status = "SUCCESS" if result.get("status") == "completed" else "SKIPPED"
        now = datetime.now(timezone.utc)
        
        status_data = {
            "name": name,
            "type": "scraper",
            "status": status,
            "last_checked": now,
            "details": result
        }
        
        history_data = {
            "service_id": service.get("service_id", "stock_scraper"),
            "service_name": name,
            "timestamp": now,
            "status": "success" if status == "SUCCESS" else "failure",
            "latency_ms": 0,
            "failure_reason": None if status == "SUCCESS" else "Scraper skipped or did not complete"
        }
        
        db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
        db["monitoring_history"].insert_one(history_data)
        
        # Evaluate alert rules
        try:
            from services.alert_service import evaluate_scraper_result
            evaluate_scraper_result(service, status_data, mongo_client)
        except Exception as alert_err:
            logging.error(f"[ScraperService] Alert evaluation failed for {name}: {alert_err}")
            
        return status_data
        
    except Exception as e:
        logging.error(f"[ScraperService] Stock Scraper encountered an error: {e}")
        now = datetime.now(timezone.utc)
        
        status_data = {
            "name": name,
            "type": "scraper",
            "status": "ERROR",
            "last_checked": now,
            "error": str(e),
            "details": {}
        }
        
        history_data = {
            "service_id": service.get("service_id", "stock_scraper"),
            "service_name": name,
            "timestamp": now,
            "status": "failure",
            "latency_ms": 0,
            "failure_reason": str(e)
        }
        
        try:
            db["latest_status"].update_one({"name": name}, {"$set": status_data}, upsert=True)
            db["monitoring_history"].insert_one(history_data)
            
            # Evaluate alert rules
            from services.alert_service import evaluate_scraper_result
            evaluate_scraper_result(service, status_data, mongo_client)
        except Exception as db_err:
            logging.error(f"[ScraperService] Failed to write error scraper status to database or evaluate alerts: {db_err}")
            
        return status_data
