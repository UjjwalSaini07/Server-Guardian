import os
import sys
import logging
from pymongo import MongoClient
import uvicorn
from dotenv import load_dotenv

from config import MONGO_URI
from services.monitoring_service import init_db_indexes

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

def main():
    # Initialize TTL Indexes on start
    logging.info("[Main] Connecting to MongoDB to initialize indexes...")
    try:
        mongo_client = MongoClient(MONGO_URI)
        init_db_indexes(mongo_client)
        mongo_client.close()
    except Exception as e:
        logging.error(f"[Main] Failed to initialize database indexes: {e}")

    # Start the FastAPI web dashboard server
    logging.info("[Dashboard] Starting FastAPI dashboard at http://0.0.0.0:8080")
    try:
        uvicorn.run("dashboard:app", host="0.0.0.0", port=8080, log_level="warning")
    except Exception as e:
        logging.critical(f"Dashboard server failed to start: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
