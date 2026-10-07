"""MongoDB persistence layer.

Provides a lazy connection, raw-reading inserts with a short dedup window,
minute/hourly rollups with upserts, history queries, CSV streaming and
storage statistics. Every function is safe to call when Mongo is not
configured - the dashboard keeps working, just without persistence.

Meters are kept in separate "sites" (polyhouse / hydroponics). Each site owns
its own raw + rollup collections, and every query is device-scoped, so data
from two meters can never mix.
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
_last_minute_done = {}
_last_hour_done = {}

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
    seen = set()
    for site, colls in config.SITE_COLLECTIONS.items():
        raw = _db[colls["raw"]]
        raw.create_index([("ts", ASCENDING)], expireAfterSeconds=config.TTL_RAW_DAYS * 86400)
        raw.create_index([("device_id", ASCENDING), ("ts", DESCENDING)])

        one = _db[colls["one"]]
        one.create_index([("ts", ASCENDING)], expireAfterSeconds=config.TTL_1MIN_DAYS * 86400)
        one.create_index([("device_id", ASCENDING), ("ts", DESCENDING)], unique=False)

        hourly = _db[colls["hourly"]]
        hourly.create_index([("device_id", ASCENDING), ("ts", DESCENDING)])
        hourly.create_index([("ts", ASCENDING)])
        seen.add(site)

    events = _db[config.EVENTS_DB]
    events.create_index([("ts", ASCENDING)])
    events.create_index([("device_id", ASCENDING), ("ts", DESCENDING)])


def is_connected():
    return _connected


def _now():
    return datetime.now(timezone.utc)


def _site_colls(device_id=None, site=None):
    site = site or config.site_for(device_id)
    return config.SITE_COLLECTIONS.get(site) or config.SITE_COLLECTIONS["polyhouse"]


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
        "device_name": config.device_name(device_id, device_name or "Breaker"),
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
        "site": config.site_for(device_id),
    }
    coll = _site_colls(device_id)["raw"]
    try:
        _db[coll].insert_one(doc)
        return True
    except PyMongoError as exc:
        print(f"[db] insert failed: {exc}")
        return False


def _floor_minute(dt):
    return dt.replace(second=0, microsecond=0)


def _floor_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def run_rollups():
    if _db is None:
        return
    with _rollup_lock:
        for site in config.SITE_COLLECTIONS:
            try:
                _rollup_minute(site)
            except Exception as exc:
                print(f"[db] minute rollup {site} failed: {exc}")
            try:
                _rollup_hour(site)
            except Exception as exc:
                print(f"[db] hour rollup {site} failed: {exc}")


def _rollup_minute(site):
    raw = _db[config.SITE_COLLECTIONS[site]["raw"]]
    one = _db[config.SITE_COLLECTIONS[site]["one"]]
    now = _now()
    target = _floor_minute(now) - timedelta(minutes=1)
    last = _last_minute_done.get(site)
    if last is not None and last >= target:
        return
    if last is None:
        start = target - timedelta(minutes=2)
    else:
        start = last + timedelta(minutes=1)
    start = max(start, target - timedelta(minutes=60))
    cur = start
    while cur <= target:
        _aggregate_window_py(raw, one, cur, cur + timedelta(minutes=1), "minute", site)
        cur += timedelta(minutes=1)
    _last_minute_done[site] = target


def _rollup_hour(site):
    one = _db[config.SITE_COLLECTIONS[site]["one"]]
    hourly = _db[config.SITE_COLLECTIONS[site]["hourly"]]
    now = _now()
    hstart = _floor_hour(now) - timedelta(hours=1)
    last = _last_hour_done.get(site)
    if last is not None and last >= hstart:
        return
    if last is None:
        start = hstart - timedelta(hours=6)
    else:
        start = last + timedelta(hours=1)
    start = max(start, hstart - timedelta(hours=6))
    allowed = config.site_devices(site)
    cur = start
    while cur <= hstart:
        _aggregate_window_py(one, hourly, cur, cur + timedelta(hours=1), "hour", site)
        if allowed:
            try:
                one.delete_many({"ts": {"$gte": cur, "$lt": cur + timedelta(hours=1)},
                                 "device_id": {"$in": allowed}})
            except PyMongoError as exc:
                print(f"[db] hour clean {site} failed: {exc}")
        cur += timedelta(hours=1)
    _last_hour_done[site] = hstart


def _aggregate_window_py(source, dest, start, end, step, site):
    """Deterministic Python-side rollup for one window (no Mongo $group)."""
    allowed = config.site_devices(site)
    if not allowed:
        return
    query = {"ts": {"$gte": start, "$lt": end}, "device_id": {"$in": allowed}}
    groups = {}
    try:
        for doc in source.find(query):
            did = doc.get("device_id")
            g = groups.setdefault(did, {
                "n": 0,
                "vsum": 0.0, "amin": None, "amax": None, "asum": 0.0,
                "vmin": None, "vmax": None, "wsum": 0.0,
                "wmin": None, "wmax": None, "tsum": 0.0, "lmax": None,
                "kwh": None, "switch": None, "last_ts": None,
            })
            v = doc.get("v_avg") if step == "hour" else doc.get("v")
            a = doc.get("a_avg") if step == "hour" else doc.get("a")
            w = doc.get("w_avg") if step == "hour" else doc.get("w")
            temp = doc.get("temp_avg") if step == "hour" else doc.get("temp")
            leak = doc.get("leak_max") if step == "hour" else doc.get("leak")
            g["n"] += 1
            if v is not None:
                g["vsum"] += v
                g["vmin"] = v if g["vmin"] is None else min(g["vmin"], v)
                g["vmax"] = v if g["vmax"] is None else max(g["vmax"], v)
            if a is not None:
                g["asum"] += a
                g["amin"] = a if g["amin"] is None else min(g["amin"], a)
                g["amax"] = a if g["amax"] is None else max(g["amax"], a)
            if w is not None:
                g["wsum"] += w
                g["wmin"] = w if g["wmin"] is None else min(g["wmin"], w)
                g["wmax"] = w if g["wmax"] is None else max(g["wmax"], w)
            if temp is not None:
                g["tsum"] += temp
            if leak is not None:
                g["lmax"] = leak if g["lmax"] is None else max(g["lmax"], leak)
            kwh = doc.get("kwh")
            if kwh is not None:
                g["kwh"] = kwh
            ts = doc.get("ts")
            if g["last_ts"] is None or (ts and ts > g["last_ts"]):
                g["last_ts"] = ts
                g["switch"] = doc.get("switch")
    except PyMongoError as exc:
        print(f"[db] rollup read {site} {step} failed: {exc}")
        return

    for did, g in groups.items():
        n = g["n"] or 1
        dset = {
            "ts": start,
            "step": step,
            "site": site,
            "count": g["n"],
            "v_avg": round(g["vsum"] / n, 2) if g["vsum"] else None,
            "v_min": g["vmin"], "v_max": g["vmax"],
            "a_avg": round(g["asum"] / n, 4) if g["asum"] else None,
            "a_min": g["amin"], "a_max": g["amax"],
            "w_avg": round(g["wsum"] / n, 1) if g["wsum"] else None,
            "w_min": g["wmin"], "w_max": g["wmax"],
            "kwh": g["kwh"],
            "temp_avg": round(g["tsum"] / n, 1) if g["tsum"] else None,
            "leak_max": g["lmax"],
            "switch": g["switch"],
        }
        try:
            dest.update_one({"device_id": did, "ts": start}, {"$set": dset}, upsert=True)
        except PyMongoError as exc:
            print(f"[db] rollup write {site} {step} failed: {exc}")
            continue
    if groups:
        print(f"[db] rollup {step} {site} {start:%Y-%m-%d %H:%M} <- {len(groups)} device(s)")


def history(range_hours, device_id=None):
    if _db is None:
        return {"success": False, "error": "DB not connected"}
    start = _now() - timedelta(hours=range_hours)
    return _series_query(start, _now(), range_hours, device_id=device_id)


def history_between(from_ts, to_ts, device_id=None):
    if _db is None or to_ts <= from_ts:
        return {"success": False, "error": "DB not connected" if _db is None else "to must be after from"}
    hours = max((to_ts - from_ts).total_seconds() / 3600.0, 0.05)
    return _series_query(from_ts, to_ts, hours, device_id=device_id)


def _series_query(start, end, range_hours, device_id=None):
    site = config.site_for(device_id)
    coll_name = _map_range_to_collection(range_hours, site)
    query = {"ts": {"$gte": start, "$lte": end}}
    if device_id:
        query["device_id"] = device_id
    elif site:
        query["device_id"] = {"$in": config.site_devices(site)}
    cursor = _db[coll_name].find(
        query,
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


def _map_range_to_collection(range_hours, site="polyhouse"):
    colls = config.SITE_COLLECTIONS.get(site) or config.SITE_COLLECTIONS["polyhouse"]
    if range_hours <= 2:
        return colls["raw"]
    if range_hours <= 48:
        return colls["one"]
    return colls["hourly"]


def last_raw(device_id):
    if _db is None:
        return None
    coll = _site_colls(device_id)["raw"]
    return _db[coll].find_one({"device_id": device_id}, sort=[("ts", DESCENDING)])


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


def query_events(from_ts, to_ts=None, device_id=None, limit=100000):
    if _db is None:
        return []
    query = {"ts": {"$gte": from_ts}}
    if to_ts is not None:
        query["ts"]["$lte"] = to_ts
    if device_id:
        query["device_id"] = device_id
    try:
        return list(_db[config.EVENTS_DB].find(query).sort("ts", ASCENDING).limit(limit))
    except PyMongoError as exc:
        print(f"[db] query_events failed: {exc}")
        return []


def event_stats(range_hours, device_id=None):
    if _db is None:
        return {"success": False, "error": "DB not connected"}
    now = _now()
    start = now - timedelta(hours=range_hours)
    events = query_events(start, now, device_id=device_id)

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
              "restores": sum(b["restores"] for b in buckets.values()),
              "netdowns": sum(1 for ev in events if ev.get("event_type") == "network_off")}

    return {"success": True, "range_hours": range_hours, "bucket_sec": bucket_sec,
            "series": series, "recent": recent, "totals": totals}


def consumption(from_ts, to_ts, device_id=None):
    if _db is None:
        return {"devices": [], "total_kwh": 0.0}
    site = config.site_for(device_id)
    coll = _db[config.SITE_COLLECTIONS[site]["raw"]]
    query = {"ts": {"$gte": from_ts, "$lte": to_ts}}
    if device_id:
        query["device_id"] = device_id
    else:
        query["device_id"] = {"$in": config.site_devices(site)}
    try:
        device_ids = coll.distinct("device_id", query)
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


def query_docs(coll_name, from_ts, to_ts, limit=500000, device_id=None):
    if _db is None:
        return []
    query = {"ts": {"$gte": from_ts, "$lt": to_ts}}
    if device_id:
        query["device_id"] = device_id
    try:
        return list(_db[coll_name].find(query).sort("ts", ASCENDING).limit(limit))
    except PyMongoError as exc:
        print(f"[db] query_docs failed: {exc}")
        return []


def iter_readings(from_ts, to_ts, device_id=None, limit=400000):
    if _db is None:
        return
    site = config.site_for(device_id)
    range_hours = (to_ts - from_ts).total_seconds() / 3600.0
    coll = _db[_map_range_to_collection(range_hours, site)]
    query = {"ts": {"$gte": from_ts, "$lt": to_ts}}
    if device_id:
        query["device_id"] = device_id
    else:
        query["device_id"] = {"$in": config.site_devices(site)}
    for doc in coll.find(query).sort("ts", ASCENDING).limit(limit):
        yield doc


def db_stats():
    if _db is None:
        return {"success": False, "connected": False, "error": "DB not connected"}
    stats = {}
    total_bytes = 0
    names = []
    for site, colls in config.SITE_COLLECTIONS.items():
        for key in ("raw", "one", "hourly"):
            names.append(colls[key])
    for name in names:
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