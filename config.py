import os
from dotenv import load_dotenv

from pathlib import Path
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

# MongoDB URI (Atlas or Local)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Load service URLs from environment variables
QUILLIX_API_URL = os.getenv("QUILLIX_API_URL") or os.getenv("API_URL")
AFFILIATE_HEALTH_URL = os.getenv("AFFILIATE_HEALTH_URL")
STOCK_SENTINEL_URL = os.getenv("STOCK_SENTINEL_URL")
VISIONRETAIL_IQ_URL = os.getenv("VISIONRETAIL_IQ_URL")
NEXORA_SERVER_URL = os.getenv("NEXORA_SERVER_URL")

# Unified list of monitored services and scraping workers
SERVICES_CONFIG = [
    {
        "service_id": "quillix_api",
        "name": "NexGen Quillix Server",
        "type": "pinger",
        "url": QUILLIX_API_URL,
        "enabled": True,
        "interval_minutes": 10,  # 10 minutes keeps Render awake (threshold is 15)
        "db_name": "ServerAutomation",
        "collection_name": "health_logs",
        "log_expiry_hours": 1,
        "allowed_hours_ist": (9, 2),  # 9:00 AM to 2:00 AM IST
        "allowed_days": [0, 1, 2, 3, 4, 5, 6],  # Mon-Sun
        "parse_analytics": False
    },
    {
        "service_id": "affiliate_health",
        "name": "Affiliate MVP Server",
        "type": "pinger",
        "url": AFFILIATE_HEALTH_URL,
        "enabled": True,
        "interval_minutes": 10,
        "db_name": "ServerAutomation",
        "collection_name": "health_logs",
        "log_expiry_hours": 1,
        "allowed_hours_ist": (9, 2),  # 9:00 AM to 2:00 AM IST
        "allowed_days": [0, 1, 2, 3, 4, 5, 6],  # Mon-Sun
        "parse_analytics": False
    },
    {
        "service_id": "stock_sentinel",
        "name": "Stock Sentinel Server",
        "type": "pinger",
        "url": STOCK_SENTINEL_URL,
        "enabled": True,
        "interval_minutes": 10,
        "db_name": "ServerAutomation",
        "collection_name": "health_logs",
        "log_expiry_hours": 1,
        "allowed_hours_ist": None,  # 24/7 Keep Alive
        "allowed_days": [0, 1, 2, 3, 4, 5, 6],
        "parse_analytics": True,
        "health_schema": {
            "required_fields": ["status"],
            "expected_values": {"status": "healthy"}
        }
    },
    {
        "service_id": "visionretail_iq",
        "name": "Vision Retail IQ Server",
        "type": "pinger",
        "url": VISIONRETAIL_IQ_URL,
        "enabled": True,
        "interval_minutes": 10,
        "db_name": "ServerAutomation",
        "collection_name": "health_logs",
        "log_expiry_hours": 1,
        "allowed_hours_ist": None,  # 24/7 Keep Alive
        "allowed_days": [0, 1, 2, 3, 4, 5, 6],
        "parse_analytics": True,
        "health_schema": {
            "required_fields": ["status"],
            "expected_values": {"status": "healthy"}
        }
    },
    {
        "service_id": "nexora_server",
        "name": "Nexora AI Server",
        "type": "pinger",
        "url": NEXORA_SERVER_URL,
        "enabled": True,
        "interval_minutes": 10,
        "db_name": "ServerAutomation",
        "collection_name": "health_logs",
        "log_expiry_hours": 1,
        "allowed_hours_ist": None,  # 24/7 Keep Alive
        "allowed_days": [0, 1, 2, 3, 4, 5, 6],
        "parse_analytics": True,
        "health_schema": {
            "required_fields": ["status"],
            "expected_values": {"status": "ok"}
        }
    }
]

ENABLE_STOCK_SCRAPER = os.getenv("ENABLE_STOCK_SCRAPER", "true").lower() in ("1", "true", "yes")

# Enable/Disable Latency Alerts via environment variable (defaults to False)
LATENCY_PASS = os.getenv("LATENCY_PASS", "false").lower() in ("1", "true", "yes")

# Number of days to retain monitoring history logs in the database (defaults to 30)
HISTORY_CLEANUP_DAYS = int(os.getenv("HISTORY_CLEANUP_DAYS", "30"))

if ENABLE_STOCK_SCRAPER:
    SERVICES_CONFIG.append({
        "service_id": "stock_scraper",
        "name": "Stock Scraper",
        "type": "scraper",
        "enabled": True,
        "interval_minutes": 15,
        "db_name": "investiqdb",
        "collection_name": "Stocks",
        "allowed_hours_ist": (9, 16),  # Mon-Fri 9:30 AM to 4:00 PM IST (represented roughly as 9-16 hour block)
        "allowed_days": [0, 1, 2, 3, 4],  # Mon-Fri
        "ticker_file_path": "servers/IndianStockTicker.json"
    })
