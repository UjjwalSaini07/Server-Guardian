import os
import sys
import logging
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import MongoClient

# Add current directory to path so we can import services and config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import SERVICES_CONFIG, MONGO_URI
from services.monitoring_service import execute_ping, init_db_indexes
from services.scraper_service import run_scraper

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

def run_all_jobs():
    """Main execution entrypoint for GitHub Actions monitoring runner."""
    from pathlib import Path
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    
    # Connect to MongoDB
    logging.info("[Runner] Connecting to MongoDB...")
    mongo_client = MongoClient(MONGO_URI)
    
    # Ping dashboard to keep it awake if DASHBOARD_URL is set
    dashboard_url = os.getenv("DASHBOARD_URL")
    if dashboard_url:
        try:
            logging.info(f"[Runner] Keeping dashboard awake by pinging: {dashboard_url}...")
            import requests
            requests.get(dashboard_url, timeout=30)
        except Exception as e:
            logging.warning(f"[Runner] Failed to ping dashboard: {e}")
    
    db = mongo_client["ServerAutomation"]
    status_col = db["github_actions_status"]
    
    now = datetime.now(timezone.utc)
    
    # 1. Update action status to RUNNING
    logging.info("[Runner] Recording execution start in database...")
    status_col.update_one(
        {"_id": "runner_status"},
        {
            "$set": {
                "last_execution_time": now,
                "last_status": "RUNNING",
                "error": None
            }
        },
        upsert=True
    )
    
    # Initialize indexes
    init_db_indexes(mongo_client)
    
    threads = []
    
    def run_job(service):
        try:
            name = service["name"]
            if not service.get("enabled", True):
                logging.info(f"[Runner] Skipping disabled service: {name}")
                return
                
            if service["type"] == "pinger":
                logging.info(f"[Runner] Starting ping check for {name}...")
                execute_ping(service, mongo_client)
            elif service["type"] == "scraper":
                logging.info(f"[Runner] Starting scraper check for {name}...")
                run_scraper(service, mongo_client)
        except Exception as e:
            logging.error(f"[Runner] Error executing job for {service['name']}: {e}")
            raise e

    # Start all jobs concurrently
    for s in SERVICES_CONFIG:
        if s.get("enabled", True):
            t = threading.Thread(target=run_job, args=(s,), name=f"run-{s['name']}")
            t.start()
            threads.append(t)
            
    # Wait for all jobs to complete
    for t in threads:
        t.join()
        
    logging.info("[Runner] All jobs completed.")
    
    # Run Uptime and Reliability Aggregator
    try:
        from services.uptime_aggregator import aggregate_metrics
        aggregate_metrics(mongo_client)
    except Exception as e:
        logging.error(f"[Runner] Failed to run uptime aggregation: {e}")

    # Auto-dispatch weekly / monthly executive reports
    _maybe_send_reports(mongo_client)
    
    # 2. Update status to SUCCESS
    now_end = datetime.now(timezone.utc)
    status_col.update_one(
        {"_id": "runner_status"},
        {
            "$set": {
                "last_success_time": now_end,
                "last_status": "SUCCESS",
                "error": None
            }
        },
        upsert=True
    )
    
    mongo_client.close()
    logging.info("[Runner] Database connection closed. Exit cleanly.")

def _maybe_send_reports(mongo_client):
    """
    Dispatch weekly / monthly reports if the current run falls within
    the configured dispatch window. Idempotent — safe to call on every run.
    """
    import os
    REPORT_SEND_DAY = int(os.getenv("REPORT_SEND_DAY", "0"))    # 0=Monday
    REPORT_SEND_HOUR = int(os.getenv("REPORT_SEND_HOUR_UTC", "6"))

    now = datetime.now(timezone.utc)

    # Only dispatch during the configured dispatch window
    if now.hour != REPORT_SEND_HOUR:
        return

    try:
        from services.report_service import (
            generate_weekly_report, generate_monthly_report,
            should_send_report, log_report_sent
        )
        from services.email_provider import (
            send_alert_email,
            format_weekly_report_template,
            format_monthly_report_template,
        )

        # Weekly — send on the configured day of week
        if now.weekday() == REPORT_SEND_DAY:
            week_key = now.strftime("%Y-W%W")
            if should_send_report("weekly", week_key, mongo_client):
                logging.info("[Runner] Generating weekly report...")
                report = generate_weekly_report(mongo_client)
                html = format_weekly_report_template(report)
                sent = send_alert_email(
                    f"📊 ServerGuardian Weekly Report — {report.get('period', '')}",
                    html
                )
                log_report_sent("weekly", week_key, sent, mongo_client)
                logging.info(f"[Runner] Weekly report dispatched (sent={sent})")

        # Monthly — send on the 1st of each month
        if now.day == 1:
            month_key = now.strftime("%Y-%m")
            if should_send_report("monthly", month_key, mongo_client):
                logging.info("[Runner] Generating monthly report...")
                report = generate_monthly_report(mongo_client)
                html = format_monthly_report_template(report)
                sent = send_alert_email(
                    f"📊 ServerGuardian Monthly Report — {report.get('period', '')}",
                    html
                )
                log_report_sent("monthly", month_key, sent, mongo_client)
                logging.info(f"[Runner] Monthly report dispatched (sent={sent})")

    except Exception as e:
        logging.error(f"[Runner] Report dispatch failed: {e}")


if __name__ == "__main__":
    try:
        run_all_jobs()
    except Exception as e:
        logging.critical(f"[Runner] Uncaught critical exception: {e}")
        # Update action status to FAILED in database
        try:
            mongo_client = MongoClient(MONGO_URI)
            db = mongo_client["ServerAutomation"]
            status_col = db["github_actions_status"]
            status_col.update_one(
                {"_id": "runner_status"},
                {
                    "$set": {
                        "last_failure_time": datetime.now(timezone.utc),
                        "last_status": "FAILED",
                        "error": str(e)
                    }
                },
                upsert=True
            )
            mongo_client.close()
        except Exception as db_err:
            logging.error(f"[Runner] Failed to record runner failure in database: {db_err}")
        sys.exit(1)
