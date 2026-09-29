"""
CCA Power Meter Dashboard - Backend Server
Fetches real-time power data from Tuya Cloud API, logs readings to MongoDB
and serves the dashboard.
"""

import base64
import csv
import io
import os
import time
import hmac
import hashlib
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests as http_requests
from flask import Flask, jsonify, Response, send_from_directory, request
from flask_cors import CORS

import config
import db
import exporter
import prepaid

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

IST = ZoneInfo(config.IST_ZONE)
API_TIMEOUT = 12

_token_cache = {"access_token": None, "expire_time": 0}
_last_raw_cache = {}
_last_all_status = []
_last_ev_state = {}
_prev_online = {}


def _make_signed_request(method, path, token=None, timeout=API_TIMEOUT):
    t = str(int(time.time() * 1000))
    content_hash = hashlib.sha256(b"").hexdigest()
    str_to_sign = method.upper() + "\n" + content_hash + "\n\n" + path

    if token:
        sign_str = config.TUYA_ACCESS_ID + token + t + str_to_sign
    else:
        sign_str = config.TUYA_ACCESS_ID + t + str_to_sign

    sign = hmac.new(
        config.TUYA_ACCESS_SECRET.encode(), sign_str.encode(), hashlib.sha256
    ).hexdigest().upper()

    headers = {
        "client_id": config.TUYA_ACCESS_ID,
        "t": t,
        "sign": sign,
        "sign_method": "HMAC-SHA256",
    }
    if token:
        headers["access_token"] = token

    resp = http_requests.get(f"{config.TUYA_API_ENDPOINT}{path}", headers=headers, timeout=timeout)
    return resp.json()


def _get_token():
    now = int(time.time() * 1000)
    if _token_cache["access_token"] and now < _token_cache["expire_time"]:
        return _token_cache["access_token"]

    data = _make_signed_request("GET", "/v1.0/token?grant_type=1")
    if data.get("success"):
        _token_cache["access_token"] = data["result"]["access_token"]
        _token_cache["expire_time"] = now + data["result"]["expire_time"] * 1000 - 60000
        return _token_cache["access_token"]
    raise Exception(f"Token error: {data}")


def tuya_get(path):
    def call(token):
        return _make_signed_request("GET", path, token=token)

    token = _get_token()
    data = call(token)
    if data.get("success"):
        return data
    if data.get("code") in (1010, 1011, 1106):
        _token_cache["access_token"] = None
        token = _get_token()
        return call(token)
    return data


def decode_phase_data(b64_value):
    if not b64_value:
        return None
    try:
        data = base64.b64decode(b64_value)
    except Exception:
        return None

    if len(data) >= 2 and data[0] in (0x01, 0x02) and data[1] == 0x0F:
        return None

    if len(data) < 8:
        return None
    voltage = int.from_bytes(data[0:2], "big") / 10
    current = int.from_bytes(data[2:5], "big") / 1000
    power = int.from_bytes(data[5:8], "big")
    result = {
        "voltage": round(voltage, 1),
        "current": round(current, 3),
        "power": power,
    }
    if len(data) >= 10:
        leakage = int.from_bytes(data[8:10], "big")
        result["leakage_current"] = leakage
    return result


def process_device_status(status_list):
    raw_map = {}
    for item in status_list:
        raw_map[item["code"]] = item["value"]

    metrics = {}
    for code in ("phase_a", "phase_b", "phase_c"):
        decoded = decode_phase_data(raw_map.get(code))
        if decoded:
            metrics[code] = decoded

    fe = raw_map.get("total_forward_energy")
    if isinstance(fe, (int, float)):
        metrics["total_energy"] = {"value": round(fe / 100, 2), "unit": "kWh"}

    re = raw_map.get("reverse_energy_total")
    if isinstance(re, (int, float)):
        metrics["reverse_energy"] = {"value": round(re / 100, 2), "unit": "kWh"}

    tc = raw_map.get("temp_current")
    if isinstance(tc, (int, float)):
        metrics["temperature"] = {"value": tc, "unit": "°C"}

    lc = raw_map.get("leakage_current")
    if isinstance(lc, (int, float)):
        metrics["leakage_current"] = {"value": lc, "unit": "mA"}

    metrics["switch"] = {"value": raw_map.get("switch")}
    metrics["fault"] = prepaid.decode_fault(raw_map.get("fault"))
    metrics["prepaid"] = prepaid.build_prepaid(raw_map)

    return metrics


def _maybe_log_events(dev):
    dev_id = dev["id"]
    online = bool(dev.get("online"))

    prev_online = _prev_online.get(dev_id)
    if prev_online is None:
        _prev_online[dev_id] = online
    elif prev_online != online:
        if not online:
            db.log_event(dev_id, "network_off", detail="device network off")
        else:
            db.log_event(dev_id, "network_on", detail="device back online")
        _prev_online[dev_id] = online

    metrics = dev.get("metrics") or {}
    switch = metrics.get("switch", {}).get("value")
    fault_info = metrics.get("fault") or {}
    fault_val = fault_info.get("value")
    detail = ", ".join(fault_info.get("active") or []) or (str(fault_val) if fault_val else None)

    state = _last_ev_state.get(dev_id)
    if state is None:
        last = db.last_raw(dev_id)
        if last is not None:
            _last_ev_state[dev_id] = {
                "switch": last.get("switch"),
                "fault": last.get("fault"),
            }
            state = _last_ev_state[dev_id]
        else:
            _last_ev_state[dev_id] = {"switch": switch, "fault": fault_val}
            return

    pswitch, pfault = state["switch"], state["fault"]
    state["switch"] = switch
    state["fault"] = fault_val

    if pswitch is not None and switch is not None and pswitch != switch:
        db.log_event(dev_id, "power_cut" if switch is False else "power_restored")
    if pfault is not None and fault_val is not None and pfault != fault_val:
        if fault_val:
            db.log_event(dev_id, "fault", detail=detail or f"0x{fault_val:X}")
        else:
            db.log_event(dev_id, "fault_cleared")


def _device_summaries(devices):
    results = []
    for device in devices:
        dev_id = device.get("id") or ""
        if not dev_id:
            continue
        status_data = tuya_get(f"/v1.0/devices/{dev_id}/status")
        raw_status = status_data.get("result", []) if status_data.get("success") else []
        metrics = process_device_status(raw_status)
        results.append({
            "id": dev_id,
            "name": device.get("name", "Unknown"),
            "product_name": device.get("productName", ""),
            "online": device.get("isOnline", False),
            "category": device.get("category", ""),
            "model": device.get("model", ""),
            "raw_status": raw_status,
            "metrics": metrics,
        })
    return results


@app.route("/")
def index():
    resp = send_from_directory(app.static_folder, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/api/devices")
def get_devices():
    try:
        data = tuya_get("/v2.0/cloud/thing/device?page_no=1&page_size=20")
        if data.get("success"):
            return jsonify({"success": True, "devices": data.get("result", [])})
        return jsonify({"success": False, "error": data.get("msg", "Unknown error")})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/device/<device_id>/status")
def get_device_status(device_id):
    try:
        data = tuya_get(f"/v1.0/devices/{device_id}/status")
        if data.get("success"):
            raw_status = data["result"]
            metrics = process_device_status(raw_status)
            return jsonify({"success": True, "raw": raw_status, "metrics": metrics})
        return jsonify({"success": False, "error": data.get("msg", "Unknown error")})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/all-status")
def get_all_status():
    try:
        devices_data = tuya_get("/v2.0/cloud/thing/device?page_no=1&page_size=20")
        if not devices_data.get("success"):
            return jsonify({"success": False, "error": devices_data.get("msg", "Unknown error")})

        devices = devices_data.get("result", [])
        results = _device_summaries(devices)
        global _last_all_status
        _last_all_status = results

        for dev in results:
            _maybe_log_events(dev)
            if dev["online"]:
                db.log_reading(dev["id"], dev["name"], dev["metrics"])
        db.run_rollups()

        return jsonify({"success": True, "devices": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/history")
def get_history():
    from_arg = request.args.get("from")
    to_arg = request.args.get("to")
    if from_arg and to_arg:
        try:
            from_ts = datetime.fromtimestamp(int(from_arg), tz=timezone.utc)
            to_ts = datetime.fromtimestamp(int(to_arg), tz=timezone.utc)
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "from/to must be unix timestamps"})
        if to_ts <= from_ts:
            return jsonify({"success": False, "error": "to must be after from"})
        return jsonify(db.history_between(from_ts, to_ts))

    range_name = request.args.get("range", "24h")
    range_map = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}
    range_hours = range_map.get(range_name)
    if not range_hours:
        return jsonify({"success": False, "error": "invalid range"})
    return jsonify(db.history(range_hours))


def _row_for(doc):
    step = doc.get("step")
    ts = doc["ts"]
    local = ts.astimezone(IST).replace(tzinfo=None)
    local_str = local.strftime("%Y-%m-%d %H:%M:%S")
    if step == "minute":
        return [
            local_str,
            int(ts.timestamp() * 1000),
            doc.get("device_id", ""),
            doc.get("v_avg"), doc.get("v_min"), doc.get("v_max"),
            doc.get("a_avg"), doc.get("a_min"), doc.get("a_max"),
            doc.get("w_avg"), doc.get("w_min"), doc.get("w_max"),
            doc.get("kwh"), doc.get("temp_avg"), doc.get("leak_max"),
        ]
    if step == "hour":
        return [
            local_str,
            int(ts.timestamp() * 1000),
            doc.get("device_id", ""),
            doc.get("v_avg"), None, None,
            doc.get("a_avg"), None, None,
            doc.get("w_avg"), None, None,
            doc.get("kwh"), doc.get("temp_avg"), doc.get("leak_max"),
        ]
    return [
        local_str,
        int(ts.timestamp() * 1000),
        doc.get("device_id", ""),
        doc.get("v"), None, None,
        doc.get("a"), None, None,
        doc.get("w"), None, None,
        doc.get("kwh"), doc.get("temp"), doc.get("leak"),
    ]


def _parse_export_args():
    now = datetime.now(timezone.utc)
    from_arg = request.args.get("from")
    to_arg = request.args.get("to")
    try:
        to_ts = datetime.fromtimestamp(int(to_arg), tz=timezone.utc) if to_arg else now
        from_ts = datetime.fromtimestamp(int(from_arg), tz=timezone.utc) if from_arg else now - timedelta(days=1)
    except (ValueError, TypeError):
        return None, None
    return from_ts, to_ts


@app.route("/api/export")
def api_export():
    if request.args.get("pass") != config.EXPORT_PASSWORD:
        return jsonify({"success": False, "error": "Wrong password"}), 401
    format_name = request.args.get("format", "csv")
    interval = request.args.get("interval", "raw")
    fields_arg = request.args.get("fields", "")
    fields_all = {"v", "a", "w", "pf", "kwh", "temp", "leak"}

    from_ts, to_ts = _parse_export_args()
    if from_ts is None:
        return jsonify({"success": False, "error": "from/to must be unix timestamps"})
    if not db.is_connected():
        return jsonify({"success": False, "error": "DB not connected"})
    if interval not in exporter.WINDOW_SEC:
        return jsonify({"success": False, "error": "invalid interval"})

    fields = {f for f in fields_arg.split(",") if f}
    if not fields:
        fields = set(fields_all)
    columns = [f for f in ("a", "w", "v", "pf", "kwh", "temp", "leak") if f in fields and f in fields_all]

    try:
        if format_name in ("csv", "text/csv"):
            data = exporter.build_csv(from_ts, to_ts, interval, columns)
            return Response(
                data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename=readings_{interval}.csv"},
            )
        if format_name in ("xlsx", "excel"):
            data = exporter.build_xlsx(from_ts, to_ts, interval, columns)
            return Response(
                data,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename=readings_{interval}.xlsx"},
            )
        return jsonify({"success": False, "error": "format must be csv or xlsx"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/export.csv")
def export_csv():
    now = datetime.now(timezone.utc)
    from_arg = request.args.get("from")
    to_arg = request.args.get("to")
    try:
        to_ts = datetime.fromtimestamp(int(to_arg), tz=timezone.utc) if to_arg else now
        from_ts = datetime.fromtimestamp(int(from_arg), tz=timezone.utc) if from_arg else now - timedelta(days=1)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "from/to must be unix timestamps"})

    header = ["time_ist", "ts_ms", "device", "v_avg", "v_min", "v_max",
              "a_avg", "a_min", "a_max", "w_avg", "w_min", "w_max",
              "kwh", "temp_c", "leak_ma"]

    def generate():
        yield ",".join(header) + "\n"
        for doc in db.iter_readings(from_ts, to_ts):
            row = _row_for(doc)
            yield ",".join("" if v is None else str(v) for v in row) + "\n"

    if not db.is_connected():
        return jsonify({"success": False, "error": "DB not connected"})
    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=readings.csv"})


@app.route("/api/events")
def api_events():
    window_map = {"24h": 24, "7d": 168, "30d": 720}
    window = request.args.get("window", "24h")
    if window not in window_map:
        return jsonify({"success": False, "error": "invalid window"})
    if not db.is_connected():
        return jsonify({"success": False, "error": "DB not connected"})
    return jsonify(db.event_stats(window_map[window]))


@app.route("/api/consumption")
def api_consumption():
    from_arg = request.args.get("from")
    to_arg = request.args.get("to")
    try:
        from_ts = datetime.fromtimestamp(int(from_arg), tz=timezone.utc)
        to_ts = datetime.fromtimestamp(int(to_arg), tz=timezone.utc)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "from/to must be unix timestamps"})
    if not db.is_connected():
        return jsonify({"success": False, "error": "DB not connected"})
    if to_ts <= from_ts:
        return jsonify({"success": False, "error": "to must be after from"})
    result = db.consumption(from_ts, to_ts)
    return jsonify({
        "success": True,
        "from": int(from_ts.timestamp()),
        "to": int(to_ts.timestamp()),
        "total_kwh": result["total_kwh"],
        "devices": result["devices"],
    })


@app.route("/api/export-events")
def api_export_events():
    if request.args.get("pass") != config.EXPORT_PASSWORD:
        return jsonify({"success": False, "error": "Wrong password"}), 401
    format_name = request.args.get("format", "csv")
    now = datetime.now(timezone.utc)
    from_arg = request.args.get("from")
    to_arg = request.args.get("to")
    try:
        to_ts = datetime.fromtimestamp(int(to_arg), tz=timezone.utc) if to_arg else now
        from_ts = datetime.fromtimestamp(int(from_arg), tz=timezone.utc) if from_arg else now - timedelta(days=1)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "from/to must be unix timestamps"})
    if not db.is_connected():
        return jsonify({"success": False, "error": "DB not connected"})

    evs = db.query_events(from_ts, to_ts)
    if format_name in ("xlsx", "excel"):
        return Response(
            exporter.build_events_xlsx(evs),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=events.xlsx"},
        )
    return Response(
        exporter.build_events_csv(evs),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=events.csv"},
    )


@app.route("/api/db-stats")
def api_db_stats():
    return jsonify(db.db_stats())


@app.route("/api/raw-dump")
def raw_dump():
    summary = []
    for dev in _last_all_status:
        summary.append({
            "id": dev["id"],
            "name": dev["name"],
            "dump": prepaid.raw_dump(dev["raw_status"]),
            "fault": dev["metrics"].get("fault"),
            "prepaid": dev["metrics"].get("prepaid"),
        })
    return jsonify({"success": True, "devices": summary})


if __name__ == "__main__":
    if not config.TUYA_ACCESS_ID or not config.TUYA_ACCESS_SECRET:
        print("\n" + "=" * 60)
        print("ERROR: Tuya API credentials not configured!")
        print("1. Copy .env.example to .env")
        print("2. Fill in TUYA_ACCESS_ID and TUYA_ACCESS_SECRET")
        print("   (See SETUP_GUIDE.md for instructions)")
        print("=" * 60 + "\n")
    else:
        print(f"Starting CCA Power Meter Dashboard...")
        print(f"API Endpoint: {config.TUYA_API_ENDPOINT}")
        if db.connect():
            print(f"MongoDB connected: {config.MONGODB_DB}")
        else:
            print("MongoDB not connected - running without persistence")
    app.run(debug=config.FLASK_DEBUG, host=config.HOST, port=config.PORT)