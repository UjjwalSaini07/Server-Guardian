import os
import sys
import signal
import time
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

import schedule
import uvicorn

from config import SERVICES_CONFIG
from runner import (
    init_db_indexes,
    execute_ping,
    execute_scrape,
    mongo_client
)

RUN_COUNT_FILE = ".server_run_count"
load_dotenv()
PERSIST_RUN_COUNT = os.getenv("PERSIST_RUN_COUNT", "false").lower() in ("1", "true", "yes")

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

shutdown_event = threading.Event()
scheduler_thread = None

def get_run_count():
    """Return the run number. If persistence enabled, increment & store; otherwise always 1."""
    if PERSIST_RUN_COUNT:
        count = 0
        if os.path.exists(RUN_COUNT_FILE):
            try:
                with open(RUN_COUNT_FILE, "r") as f:
                    count = int(f.read().strip() or "0")
            except Exception:
                count = 0
        count += 1
        try:
            with open(RUN_COUNT_FILE, "w") as f:
                f.write(str(count))
        except Exception as e:
            logging.error(f"Unable to write run count file: {e}")
        return count
    else:
        return 1

def run_scheduler_loop():
    """Background thread running the schedule engine."""
    logging.info("[Runner] Scheduler background loop started.")
    while not shutdown_event.is_set():
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"[Runner] Error running pending schedule tasks: {e}")
        time.sleep(1)
    logging.info("[Runner] Scheduler background loop stopped.")

def initialize_scheduler():
    """Setup pinger and scraper tasks in the scheduler and run them once immediately."""
    logging.info("[Runner] Initializing scheduler tasks...")
    
    # Run a dry-run check immediately to populate the web dashboard cache right away
    logging.info("[Runner] Executing initial pings and scrapers immediately on start...")
    initial_threads = []
    
    for s in SERVICES_CONFIG:
        if not s["enabled"]:
            continue
            
        if s["type"] == "pinger":
            # Schedule periodic ping
            schedule.every(s["interval_minutes"]).minutes.do(execute_ping, s)
            # Run initial check in a thread so they execute concurrently and don't delay start
            t = threading.Thread(target=execute_ping, args=(s,), name=f"initial-ping-{s['name']}")
            t.start()
            initial_threads.append(t)
            
        elif s["type"] == "scraper":
            # Schedule periodic scraping
            schedule.every(s["interval_minutes"]).minutes.do(execute_scrape, s)
            # Run initial scraping in a thread
            t = threading.Thread(target=execute_scrape, args=(s,), name=f"initial-scrape-{s['name']}")
            t.start()
            initial_threads.append(t)
            
    # Wait up to 5 seconds for initial pings to complete so the dashboard is warm immediately
    # We don't wait for scraper because it can take minutes
    for t in initial_threads:
        if "ping" in t.name:
            t.join(timeout=5)

def shutdown(signum=None, frame=None):
    """Gracefully shutdown background threads and clean up resources."""
    ts = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"\n----Server is Terminated---- [{ts}]")
    
    logging.info("[Runner] Shutting down...")
    shutdown_event.set()
    
    # Wait for scheduler thread
    if scheduler_thread and scheduler_thread.is_alive():
        scheduler_thread.join(timeout=5)
        
    # Close mongo client
    try:
        mongo_client.close()
        logging.info("[Runner] MongoDB client closed.")
    except Exception as e:
        logging.error(f"[Runner] Error closing MongoDB client: {e}")

    # Reset run counter if persistence is disabled
    if not PERSIST_RUN_COUNT and os.path.exists(RUN_COUNT_FILE):
        try:
            os.remove(RUN_COUNT_FILE)
        except Exception as e:
            logging.debug(f"Could not remove run count file: {e}")

    logging.info("[Runner] All background tasks stopped. Exiting.")
    sys.exit(0)

if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Log Server Run Number
    run_number = get_run_count()
    ts = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S %Z")
    print("\n" + "-" * 6 + f" Server Running {run_number} " + "-" * 6)
    print(f"With Timestamp {ts}\n")

    # Initialize TTL Indexes
    init_db_indexes()

    # Initialize and configure scheduler
    initialize_scheduler()

    # Start the scheduler loop in a background thread
    scheduler_thread = threading.Thread(target=run_scheduler_loop, name="SchedulerLoop", daemon=True)
    scheduler_thread.start()

    # Start the FastAPI web dashboard server in the main thread
    logging.info("[Dashboard] Starting FastAPI dashboard at http://0.0.0.0:8080")
    try:
        uvicorn.run("dashboard:app", host="0.0.0.0", port=8080, log_level="warning")
    except KeyboardInterrupt:
        shutdown()
    except Exception as e:
        logging.critical(f"Dashboard server failed to start: {e}")
        shutdown()
