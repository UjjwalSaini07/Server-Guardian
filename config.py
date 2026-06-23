import os
from dotenv import load_dotenv

load_dotenv()

# MongoDB URI (Atlas or Local)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Load service URLs from environment variables
QUILLIX_API_URL = os.getenv("QUILLIX_API_URL") or os.getenv("API_URL")
AFFILIATE_HEALTH_URL = os.getenv("AFFILIATE_HEALTH_URL")
STOCK_SENTINEL_URL = os.getenv("STOCK_SENTINEL_URL")
VISIONRETAIL_IQ_URL = os.getenv("VISIONRETAIL_IQ_URL")

# Unified list of monitored services and scraping workers
SERVICES_CONFIG = [
    {
        "name": "Quillix API",
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
        "name": "Affiliate MVP Health",
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
        "parse_analytics": True
    },
    {
        "name": "VisionRetail IQ",
        "type": "pinger",
        "url": VISIONRETAIL_IQ_URL,
        "enabled": True,
        "interval_minutes": 10,
        "db_name": "ServerAutomation",
        "collection_name": "health_logs",
        "log_expiry_hours": 1,
        "allowed_hours_ist": None,  # 24/7 Keep Alive
        "allowed_days": [0, 1, 2, 3, 4, 5, 6],
        "parse_analytics": True
    }
]

# Enable/Disable Stock Scraper via environment variable
ENABLE_STOCK_SCRAPER = os.getenv("ENABLE_STOCK_SCRAPER", "true").lower() in ("1", "true", "yes")

if ENABLE_STOCK_SCRAPER:
    SERVICES_CONFIG.append({
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
