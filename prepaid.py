"""Decoders for the prepaid / alarm features of Tuya DLQ circuit breakers.

Verification status per field:
  verified  - documented Tuya scale/format, cross-checked with live device data
  structural - the byte layout is documented (4 bytes per alarm), but the
               threshold unit for this hardware model is not confirmed
  unverified - raw values exposed until the Tuya app can be tested empirically
"""

import base64

FAULT_BITS = [
    "short_circuit_alarm",
    "surge_alarm",
    "overload_alarm",
    "leakagecurr_alarm",
    "temp_dif_fault",
    "fire_alarm",
    "high_power_alarm",
    "self_test_alarm",
    "ov_cr",
    "unbalance_alarm",
    "ov_vol",
    "undervoltage_alarm",
    "miss_phase_alarm",
    "outage_alarm",
    "magnetism_alarm",
    "credit_alarm",
    "no_balance_alarm",
]

ALARM_LABEL_HINTS = {
    0x01: "overcurrent",
    0x03: "over_voltage",
    0x04: "leakage_current",
    0x05: "under_voltage",
}


def decode_fault(value):
    if value is None or value == 0:
        return {"value": value, "active": []}
    try:
        bits = int(value)
    except (TypeError, ValueError):
        return {"value": value, "active": []}
    active = []
    for i, label in enumerate(FAULT_BITS):
        if bits & (1 << i):
            active.append(label)
    return {"value": value, "active": active}


def b64_to_hex(value):
    if not value:
        return None
    try:
        return base64.b64decode(value).hex(" ")
    except Exception:
        return None


def decode_alarm_set(value):
    hex_str = b64_to_hex(value)
    if not hex_str:
        return {"raw_hex": None, "alarms": []}
    data = base64.b64decode(value)
    alarms = []
    if len(data) % 4 != 0:
        return {"raw_hex": hex_str, "alarms": [], "note": "length not a multiple of 4"}
    for i in range(0, len(data), 4):
        presence, action, hi, lo = data[i], data[i + 1], data[i + 2], data[i + 3]
        threshold = hi * 256 + lo
        alarms.append({
            "presence": presence,
            "label": ALARM_LABEL_HINTS.get(presence, "unknown"),
            "action": action,
            "trip": action == 0x01,
            "threshold_raw": threshold,
        })
    return {"raw_hex": hex_str, "alarms": alarms, "note": "threshold units unverified for this model"}


def decode_cycle_time(value):
    hex_str = b64_to_hex(value)
    data = base64.b64decode(value) if value else b""
    result = {"raw_hex": hex_str}
    if len(data) >= 2:
        result["head_uint16_be"] = int.from_bytes(data[0:2], "big")
    if len(data) >= 2:
        result["head_uint16_le"] = int.from_bytes(data[0:2], "little")
    result["length"] = len(data)
    return result


def decode_countdown(value):
    if value is None:
        return None
    try:
        secs = int(value)
    except (TypeError, ValueError):
        return value
    if secs == 0:
        return {"seconds": 0, "human": "off"}
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return {"seconds": secs, "human": f"{h}h {m:02d}m {s:02d}s"}


def build_prepaid(raw_map):
    balance = raw_map.get("balance_energy")
    charge = raw_map.get("charge_energy")
    result = {
        "switch_prepayment": raw_map.get("switch_prepayment"),
        "balance_kwh": round(balance / 100.0, 2) if isinstance(balance, (int, float)) else balance,
        "charge_kwh": round(charge / 100.0, 2) if isinstance(charge, (int, float)) else charge,
        "countdown": decode_countdown(raw_map.get("countdown_1")),
        "cycle_time": decode_cycle_time(raw_map.get("cycle_time")),
        "energy_reset": raw_map.get("energy_reset"),
        "alarm_set_1": decode_alarm_set(raw_map.get("alarm_set_1")),
        "alarm_set_2": decode_alarm_set(raw_map.get("alarm_set_2")),
        "breaker_number": raw_map.get("breaker_number"),
    }
    return result


def raw_dump(raw_status):
    out = []
    for item in raw_status:
        code = item.get("code")
        value = item.get("value")
        entry = {"code": code, "value": value, "hex": b64_to_hex(value) if isinstance(value, str) else None}
        out.append(entry)
    return out