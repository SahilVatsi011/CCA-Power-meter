"""MongoDB persistence layer.

Provides a lazy connection, raw-reading inserts with a short dedup window,
minute/hourly rollups with upserts, history queries, CSV streaming and
storage statistics. Every function is safe to call when Mongo is not
configured - the dashboard keeps working, just without persistence.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import certifi
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError

import config

_client = None
_db = None
_connected = False
_connect_lock = threading.Lock()

_last_raw_ts = {}
_rollup_lock = threading.Lock()
_last_minute_done = None
_last_hour_done = None

DEVICE_FIELDS = {
    "v": ("phase_a", "voltage"),
    "a": ("phase_a", "current"),
    "w": ("phase_a", "power"),
    "vb": ("phase_b", "voltage"),
    "ab": ("phase_b", "current"),
    "wb": ("phase_b", "power"),
    "vc": ("phase_c", "voltage"),
    "ac": ("phase_c", "current"),
    "wc": ("phase_c", "power"),
}


def connect():
    global _client, _db, _connected
    if _connected:
        return True
    if not config.MONGODB_URI:
        return False
    with _connect_lock:
        if _connected:
            return True
        try:
            _client = MongoClient(
                config.MONGODB_URI,
                tls=config.MONGODB_TLS,
                tlsCAFile=certifi.where(),
                serverSelectionTimeoutMS=config.CONNECT_TIMEOUT_MS,
                connectTimeoutMS=config.CONNECT_TIMEOUT_MS,
            )
            _db = _client[config.MONGODB_DB]
            _client.admin.command("ping")
            _ensure_indexes()
            _connected = True
            return True
        except PyMongoError as exc:
            _client = None
            _db = None
            print(f"[db] Mongo connect failed: {exc}")
            return False


def _ensure_indexes():
    if _db is None:
        return
    raw = _db[config.SERVER_DB]
    raw.create_index([("ts", ASCENDING)], expireAfterSeconds=config.TTL_RAW_DAYS * 86400)
    raw.create_index([("device_id", ASCENDING), ("ts", DESCENDING)])

    one = _db[config.SERVER_DB_1MIN]
    one.create_index([("ts", ASCENDING)], expireAfterSeconds=config.TTL_1MIN_DAYS * 86400)
    one.create_index([("device_id", ASCENDING), ("ts", DESCENDING)], unique=False)

    hourly = _db[config.SERVER_DB_HOURLY]
    hourly.create_index([("device_id", ASCENDING), ("ts", DESCENDING)])
    hourly.create_index([("ts", ASCENDING)])

    events = _db[config.EVENTS_DB]
    events.create_index([("ts", ASCENDING)])
    events.create_index([("device_id", ASCENDING), ("ts", DESCENDING)])


def is_connected():
    return _connected


def _now():
    return datetime.now(timezone.utc)


def log_reading(device_id, device_name, metrics):
    if _db is None or not device_id:
        return False
    now = _now()
    last = _last_raw_ts.get(device_id, 0.0)
    if now.timestamp() - last < config.LOG_INTERVAL_SEC:
        return False
    _last_raw_ts[device_id] = now.timestamp()

    pa = metrics.get("phase_a") or {}
    pb = metrics.get("phase_b") or {}
    pc = metrics.get("phase_c") or {}
    te = metrics.get("total_energy") or {}
    temp = metrics.get("temperature") or {}
    leak = metrics.get("leakage_current") or {}
    switch = metrics.get("switch") or {}
    fault = metrics.get("fault") or {}
    prep = metrics.get("prepaid") or {}

    v = pa.get("voltage")
    a = pa.get("current")
    w = pa.get("power")
    pf = None
    if v and a and v * a > 0:
        pf = round(w / (v * a), 3) if w else None

    doc = {
        "device_id": device_id,
        "device_name": device_name or "Breaker",
        "ts": now,
        "v": v,
        "a": a,
        "w": w,
        "vb": pb.get("voltage"),
        "ab": pb.get("current"),
        "wb": pb.get("power"),
        "vc": pc.get("voltage"),
        "ac": pc.get("current"),
        "wc": pc.get("power"),
        "kwh": te.get("value"),
        "temp": temp.get("value"),
        "leak": leak.get("value"),
        "switch": switch.get("value"),
        "fault": fault.get("value"),
        "pf": pf,
        "balance": prep.get("balance_kwh"),
        "charge": prep.get("charge_kwh"),
        "prepaid": prep.get("switch_prepayment"),
    }
    try:
        _db[config.SERVER_DB].insert_one(doc)
        return True
    except PyMongoError as exc:
        print(f"[db] insert failed: {exc}")
        return False


def run_rollups():
    if _db is None:
        return
    with _rollup_lock:
        _rollup_minute()
        _rollup_hour()


def _rollup_minute():
    global _last_minute_done
    raw = _db[config.SERVER_DB]
    one = _db[config.SERVER_DB_1MIN]
    now = _now()
    target = datetime(now.year, now.month, now.day, now.hour, now.minute, tzinfo=timezone.utc) - timedelta(minutes=1)
    if _last_minute_done is None or _last_minute_done < target:
        for minute in range(0, 3):
            mstart = target - timedelta(minutes=minute)
            if _last_minute_done and mstart <= _last_minute_done:
                break
            mstart = datetime(mstart.year, mstart.month, mstart.day, mstart.hour, mstart.minute, tzinfo=timezone.utc)
            mend = mstart + timedelta(minutes=1)
            _aggregate_into(raw, one, mstart, mend, "minute")
        _last_minute_done = target


def _rollup_hour():
    global _last_hour_done
    one = _db[config.SERVER_DB_1MIN]
    hourly = _db[config.SERVER_DB_HOURLY]
    now = _now()
    hstart = datetime(now.year, now.month, now.day, now.hour, tzinfo=timezone.utc) - timedelta(hours=1)
    if _last_hour_done is None or _last_hour_done < hstart:
        hend = hstart + timedelta(hours=1)
        _aggregate_into(one, hourly, hstart, hend, "hour")
        result = one.delete_many({"ts": {"$gte": hstart, "$lt": hend}})
        _last_hour_done = hstart


def _aggregate_into(source, dest, start, end, step):
    pipeline = [
        {"$match": {"ts": {"$gte": start, "$lt": end}}},
        {"$group": {
            "_id": "$device_id",
            "count": {"$sum": 1},
            "v_min": {"$min": "$v"}, "v_max": {"$max": "$v"}, "v_avg": {"$avg": "$v"},
            "a_min": {"$min": "$a"}, "a_max": {"$max": "$a"}, "a_avg": {"$avg": "$a"},
            "w_min": {"$min": "$w"}, "w_max": {"$max": "$w"}, "w_avg": {"$avg": "$w"},
            "kwh": {"$max": "$kwh"},
            "temp_avg": {"$avg": "$temp"},
            "leak_max": {"$max": "$leak"},
            "switch": {"$last": "$switch"},
        }},
    ]
    try:
        docs = list(source.aggregate(pipeline))
        for d in docs:
            dset = {
                "ts": start,
                "step": step,
                "count": d["count"],
                "v_min": d["v_min"], "v_max": d["v_max"], "v_avg": round(d["v_avg"], 2) if d["v_avg"] is not None else None,
                "a_min": d["a_min"], "a_max": d["a_max"], "a_avg": round(d["a_avg"], 4) if d["a_avg"] is not None else None,
                "w_min": d["w_min"], "w_max": d["w_max"], "w_avg": round(d["w_avg"], 1) if d["w_avg"] is not None else None,
                "kwh": d["kwh"],
                "temp_avg": round(d["temp_avg"], 1) if d["temp_avg"] is not None else None,
                "leak_max": d["leak_max"],
                "switch": d["switch"],
            }
            dest.update_one(
                {"device_id": d["_id"], "ts": start},
                {"$set": dset},
                upsert=True,
            )
    except PyMongoError as exc:
        print(f"[db] rollup {step} failed: {exc}")


def history(range_hours):
    if _db is None:
        return {"success": False, "error": "DB not connected"}
    start = _now() - timedelta(hours=range_hours)
    return _series_query(start, _now(), range_hours)


def history_between(from_ts, to_ts):
    if _db is None or to_ts <= from_ts:
        return {"success": False, "error": "DB not connected" if _db is None else "to must be after from"}
    hours = max((to_ts - from_ts).total_seconds() / 3600.0, 0.05)
    return _series_query(from_ts, to_ts, hours)


def _series_query(start, end, range_hours):
    coll_name = _map_range_to_collection(range_hours)
    cursor = _db[coll_name].find(
        {"ts": {"$gte": start, "$lte": end}},
        {"device_id": 1, "ts": 1, "v": 1, "a": 1, "w": 1, "kwh": 1, "temp": 1, "pf": 1,
         "v_avg": 1, "a_avg": 1, "w_avg": 1},
    ).sort("ts", ASCENDING)
    devices = {}
    for doc in cursor:
        did = doc.get("device_id", "unknown")
        series = devices.setdefault(did, {"labels": [], "v": [], "a": [], "w": [], "kwh": [], "temp": [], "pf": []})
        ms = int(doc["ts"].timestamp() * 1000)
        series["labels"].append(ms)
        series["v"].append(doc.get("v") if doc.get("v") is not None else doc.get("v_avg"))
        series["a"].append(doc.get("a") if doc.get("a") is not None else doc.get("a_avg"))
        series["w"].append(doc.get("w") if doc.get("w") is not None else doc.get("w_avg"))
        series["kwh"].append(doc.get("kwh"))
        series["temp"].append(doc.get("temp"))
        series["pf"].append(doc.get("pf"))
    return {
        "success": True,
        "range_hours": range_hours,
        "collection": coll_name,
        "devices": devices,
    }


def _map_range_to_collection(range_hours):
    if range_hours <= 2:
        return config.SERVER_DB
    if range_hours <= 48:
        return config.SERVER_DB_1MIN
    return config.SERVER_DB_HOURLY


def last_raw(device_id):
    if _db is None:
        return None
    return _db[config.SERVER_DB].find_one({"device_id": device_id}, sort=[("ts", DESCENDING)])


def log_event(device_id, event_type, detail=None, ts=None):
    if _db is None or not device_id:
        return False
    try:
        _db[config.EVENTS_DB].insert_one({
            "device_id": device_id,
            "event_type": event_type,
            "detail": detail,
            "ts": ts or _now(),
        })
        return True
    except PyMongoError as exc:
        print(f"[db] event insert failed: {exc}")
        return False


def query_events(from_ts, to_ts=None, limit=100000):
    if _db is None:
        return []
    query = {"ts": {"$gte": from_ts}}
    if to_ts is not None:
        query["ts"]["$lte"] = to_ts
    try:
        return list(_db[config.EVENTS_DB].find(query).sort("ts", ASCENDING).limit(limit))
    except PyMongoError as exc:
        print(f"[db] query_events failed: {exc}")
        return []


def event_stats(range_hours):
    if _db is None:
        return {"success": False, "error": "DB not connected"}
    now = _now()
    start = now - timedelta(hours=range_hours)
    events = query_events(start, now)

    bucket_sec = 3600 if range_hours <= 24 else (6 * 3600 if range_hours <= 168 else 86400)
    buckets = {}
    order = []
    first = int(start.timestamp() // bucket_sec) * bucket_sec
    last = int(now.timestamp())
    n = first
    while n <= last:
        buckets[n] = {"cuts": 0, "faults": 0, "restores": 0}
        order.append(n)
        n += bucket_sec

    for ev in events:
        key = int(ev["ts"].timestamp() // bucket_sec) * bucket_sec
        if key not in buckets:
            continue
        et = ev.get("event_type")
        if et == "power_cut":
            buckets[key]["cuts"] += 1
        elif et == "power_restored":
            buckets[key]["restores"] += 1
        elif et == "fault":
            buckets[key]["faults"] += 1

    series = [{"ts_ms": key * 1000, "cuts": buckets[key]["cuts"],
               "faults": buckets[key]["faults"], "restores": buckets[key]["restores"]} for key in order]

    recent = []
    for ev in events[-24:]:
        recent.append({
            "ts_ms": int(ev["ts"].timestamp() * 1000),
            "event_type": ev.get("event_type"),
            "detail": ev.get("detail"),
        })

    totals = {"cuts": sum(b["cuts"] for b in buckets.values()),
              "faults": sum(b["faults"] for b in buckets.values()),
              "restores": sum(b["restores"] for b in buckets.values())}

    return {"success": True, "range_hours": range_hours, "bucket_sec": bucket_sec,
            "series": series, "recent": recent, "totals": totals}


def consumption(from_ts, to_ts):
    if _db is None:
        return {"devices": [], "total_kwh": 0.0}
    coll = _db[config.SERVER_DB]
    try:
        device_ids = coll.distinct("device_id", {"ts": {"$gte": from_ts, "$lte": to_ts}})
    except PyMongoError as exc:
        print(f"[db] consumption distinct failed: {exc}")
        return {"devices": [], "total_kwh": 0.0}

    total = 0.0
    details = []
    for did in device_ids:
        first = coll.find_one(
            {"device_id": did, "ts": {"$gte": from_ts}},
            projection={"kwh": 1, "ts": 1},
            sort=[("ts", ASCENDING)],
        )
        last = coll.find_one(
            {"device_id": did, "ts": {"$lte": to_ts}},
            projection={"kwh": 1, "ts": 1},
            sort=[("ts", DESCENDING)],
        )
        if not first or not last:
            continue
        k1 = first.get("kwh")
        k2 = last.get("kwh")
        if k1 is None or k2 is None:
            continue
        diff = k2 - k1
        if diff < 0:
            continue
        total += diff
        details.append({
            "device_id": did,
            "kwh_start": k1,
            "kwh_end": k2,
            "consumed": round(diff, 4),
        })
    return {"devices": details, "total_kwh": round(total, 4)}


def query_docs(coll_name, from_ts, to_ts, limit=500000):
    if _db is None:
        return []
    query = {"ts": {"$gte": from_ts, "$lt": to_ts}}
    try:
        return list(_db[coll_name].find(query).sort("ts", ASCENDING).limit(limit))
    except PyMongoError as exc:
        print(f"[db] query_docs failed: {exc}")
        return []


def iter_readings(from_ts, to_ts, limit=400000):
    if _db is None:
        return
    range_hours = (to_ts - from_ts).total_seconds() / 3600.0
    if range_hours <= 24:
        coll = _db[config.SERVER_DB]
    elif range_hours <= 720:
        coll = _db[config.SERVER_DB_1MIN]
    else:
        coll = _db[config.SERVER_DB_HOURLY]
    query = {"ts": {"$gte": from_ts, "$lt": to_ts}}
    for doc in coll.find(query).sort("ts", ASCENDING).limit(limit):
        yield doc


def db_stats():
    if _db is None:
        return {"success": False, "connected": False, "error": "DB not connected"}
    stats = {}
    total_bytes = 0
    for name in (config.SERVER_DB, config.SERVER_DB_1MIN, config.SERVER_DB_HOURLY):
        try:
            cs = _db.command("collStats", name)
        except PyMongoError:
            continue
        count = cs.get("count", 0)
        storage = cs.get("storageSize", 0) + cs.get("totalIndexSize", 0)
        total_bytes += storage
        oldest = _db[name].find_one({}, {"ts": 1}, sort=[("ts", ASCENDING)])
        newest = _db[name].find_one({}, {"ts": 1}, sort=[("ts", DESCENDING)])
        stats[name] = {
            "count": count,
            "storage_bytes": storage,
            "oldest": oldest["ts"].isoformat() if oldest else None,
            "newest": newest["ts"].isoformat() if newest else None,
        }
    pct = round(total_bytes / config.MOUNT_LIMIT_BYTES * 100, 2)
    return {
        "success": True,
        "connected": True,
        "database": config.MONGODB_DB,
        "limit_bytes": config.MOUNT_LIMIT_BYTES,
        "used_bytes": total_bytes,
        "used_percent": pct,
        "ttl_raw_days": config.TTL_RAW_DAYS,
        "ttl_1min_days": config.TTL_1MIN_DAYS,
        "collections": stats,
    }