"""CCA Power Meter Dashboard - central configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

TUYA_ACCESS_ID = os.getenv("TUYA_ACCESS_ID", "")
TUYA_ACCESS_SECRET = os.getenv("TUYA_ACCESS_SECRET", "")
TUYA_API_ENDPOINT = os.getenv("TUYA_API_ENDPOINT", "https://openapi.tuyain.com")

MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "cca_power_meter")
MONGODB_TLS = os.getenv("MONGODB_TLS", "1") == "1"
CONNECT_TIMEOUT_MS = int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "8000"))

SERVER_DB = "readings"
SERVER_DB_1MIN = "readings_1min"
SERVER_DB_HOURLY = "readings_hourly"
EVENTS_DB = "events"
EXPORT_PASSWORD = "0000"

TTL_RAW_DAYS = int(os.getenv("TTL_RAW_DAYS", "90"))
TTL_1MIN_DAYS = int(os.getenv("TTL_1MIN_DAYS", "400"))

LOG_INTERVAL_SEC = 30
MOUNT_LIMIT_BYTES = 512 * 1024 * 1024

FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
HOST = os.getenv("HOST", "::")
PORT = int(os.getenv("PORT", "5001"))

IST_ZONE = "Asia/Kolkata"