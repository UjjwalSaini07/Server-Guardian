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
    load_dotenv()
    
    # Connect to MongoDB
    logging.info("[Runner] Connecting to MongoDB...")
    mongo_client = MongoClient(MONGO_URI)
    
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
