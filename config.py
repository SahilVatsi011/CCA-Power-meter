"""CCA Power Meter Dashboard - central configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

TUYA_ACCESS_ID = os.getenv("TUYA_ACCESS_ID", "")
TUYA_ACCESS_SECRET = os.getenv("TUYA_ACCESS_SECRET", "")
TUYA_API_ENDPOINT = os.getenv("TUYA_API_ENDPOINT", "https://openapi.tuyain.com")

# Data source mode: "cloud" (Tuya Cloud API) or "lan" (tinytuya direct device)
TUYA_MODE = os.getenv("TUYA_MODE", "cloud").strip().lower()

# tinytuya LAN mode (direct device communication, no cloud needed)
TUYA_DEVICE_ID = os.getenv("TUYA_DEVICE_ID", "")
TUYA_LOCAL_KEY = os.getenv("TUYA_LOCAL_KEY", "")
TUYA_DEVICE_IP = os.getenv("TUYA_DEVICE_IP", "")
TUYA_DEVICE_NAME = os.getenv("TUYA_DEVICE_NAME", "Smart Breaker")
TUYA_PROTOCOL_VER = os.getenv("TUYA_PROTOCOL_VER", "3.3")
USE_LAN = TUYA_MODE == "lan" and bool(TUYA_DEVICE_ID and TUYA_LOCAL_KEY)

# Sustainability: poll Tuya at most every TUYA_POLL_SEC seconds in the
# background and serve the cached snapshot to browsers (keeps monthly quota
# small even with 2 devices + fast browser refresh).
TUYA_POLL_SEC = int(os.getenv("TUYA_POLL_SEC", "600"))

MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "cca_power_meter")
MONGODB_TLS = os.getenv("MONGODB_TLS", "1") == "1"
CONNECT_TIMEOUT_MS = int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "8000"))

# Device registry: every Tuya device id is mapped to a display name and a
# dedicated database "site". Each site owns its own collection set so readings
# from two meters can never mix (no cross-contamination in charts/exports).
DEVICES = {
    "d780624b94e6541fde0u16": {"name": "Polyhouse 2", "site": "polyhouse"},
    "d716467e4cec0ba95deibf": {"name": "Hydroponics Lab", "site": "hydroponics"},
    "d7cfea8b0a84ad9c65znby": {"name": "Polyhouse 1", "site": "polyhouse1"},
}

SITE_COLLECTIONS = {
    "polyhouse": {
        "raw": os.getenv("MONGO_DB_RAW", "readings"),
        "one": os.getenv("MONGO_DB_1MIN", "readings_1min"),
        "hourly": os.getenv("MONGO_DB_HOURLY", "readings_hourly"),
    },
    "hydroponics": {
        "raw": os.getenv("MONGO_DB_RAW_HYDRO", "readings_hydro"),
        "one": os.getenv("MONGO_DB_1MIN_HYDRO", "readings_hydro_1min"),
        "hourly": os.getenv("MONGO_DB_HOURLY_HYDRO", "readings_hydro_hourly"),
    },
    "polyhouse1": {
        "raw": os.getenv("MONGO_DB_RAW_P1", "readings_p1"),
        "one": os.getenv("MONGO_DB_1MIN_P1", "readings_p1_1min"),
        "hourly": os.getenv("MONGO_DB_HOURLY_P1", "readings_p1_hourly"),
    },
}

# Backwards-compatible aliases (polyhouse site keeps the original names).
SERVER_DB = SITE_COLLECTIONS["polyhouse"]["raw"]
SERVER_DB_1MIN = SITE_COLLECTIONS["polyhouse"]["one"]
SERVER_DB_HOURLY = SITE_COLLECTIONS["polyhouse"]["hourly"]
EVENTS_DB = "events"
EXPORT_PASSWORD = "0000"


def site_for(device_id):
    """Which DB site owns this device? Unknown devices default to polyhouse."""
    info = DEVICES.get(device_id or "")
    return (info or {}).get("site", "polyhouse")


def device_name(device_id, fallback="Breaker"):
    """Display name for a device id (falls back to whatever Tuya reported)."""
    info = DEVICES.get(device_id or "")
    return (info or {}).get("name") or fallback


def site_devices(site):
    """All device ids belonging to a DB site (used to filter queries)."""
    return [did for did, info in DEVICES.items() if info.get("site") == site]

TTL_RAW_DAYS = int(os.getenv("TTL_RAW_DAYS", "90"))
TTL_1MIN_DAYS = int(os.getenv("TTL_1MIN_DAYS", "400"))

LOG_INTERVAL_SEC = 30
MOUNT_LIMIT_BYTES = 512 * 1024 * 1024

FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
HOST = os.getenv("HOST", "::")
PORT = int(os.getenv("PORT", "5001"))

IST_ZONE = "Asia/Kolkata"