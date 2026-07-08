#Declaration of the ONOS port statistics monitoring module that collects telemetry data (bandwidth, packet counters) and stores it in the database with optional callback and threshold subscription support.
# telemetry.py - Monitoring ONOS port statistics and saving to database.
import threading, time, requests
from requests.auth import HTTPBasicAuth
from .onos_deployer import get_device_id_and_port_by_ip
from .db import AssuranceDB

ONOS_REST = "http://127.0.0.1:8181/onos/v1"
ONOS_USER = "onos"
ONOS_PASS = "rocks"
db = AssuranceDB()

def get_port_statistics(device_id, port):
    url = f"{ONOS_REST}/statistics/ports/{device_id}/{port}"
    resp = requests.get(url, auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS), timeout=5)
    if resp.status_code != 200:
        return None
    try:
        return resp.json()['statistics'][0]['ports'][0]
    except:
        return None

def save_telemetry(ip, device_id, port, stats):
    db.save_telemetry(ip, device_id, port, stats)

def monitor_bandwidth(ip, callback=None, interval=10, save_history=True):
    try:
        device_id, port = get_device_id_and_port_by_ip(ip)
    except Exception as e:
        print(f"[Telemetry] Cannot monitor {ip}: {e}")
        return

    state = {"time": None, "rx_bytes": 0, "tx_bytes": 0}

    def _run():
        while True:
            stats = get_port_statistics(device_id, port)
            if not stats:
                time.sleep(interval)
                continue
            rx = stats.get("bytesReceived", 0)
            tx = stats.get("bytesSent", 0)
            now = time.time()
            info = {
                "ip": ip, "device_id": device_id, "port": port,
                "timestamp": now, "rx_bytes": rx, "tx_bytes": tx,
                "packets_received": stats.get("packetsReceived", 0),
                "packets_sent": stats.get("packetsSent", 0),
                "rx_dropped": stats.get("packetsRxDropped", 0),
                "tx_dropped": stats.get("packetsTxDropped", 0),
            }
            if state["time"] is not None:
                delta_t = now - state["time"]
                if delta_t > 0:
                    info["rx_bps"] = (rx - state["rx_bytes"]) * 8 / delta_t
                    info["tx_bps"] = (tx - state["tx_bytes"]) * 8 / delta_t
            else:
                info["rx_bps"] = info["tx_bps"] = 0
            state.update({"rx_bytes": rx, "tx_bytes": tx, "time": now})
            if save_history:
                save_telemetry(ip, device_id, port, stats)
            if callback:
                callback(info)
            time.sleep(interval)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t

def subscribe_counters(device_id, port, callback, interval=10):
    def _run():
        while True:
            stats = get_port_statistics(device_id, port)
            if stats:
                callback(stats)
            time.sleep(interval)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t

def subscribe_threshold(sensor_path, threshold_value, callback):
    print(f"[Telemetry] Threshold subscription for {sensor_path} (value={threshold_value}) – not implemented via REST.")
