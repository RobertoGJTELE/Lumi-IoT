#Declaration of the ONOS port statistics monitoring module that collects telemetry data (bandwidth, packet counters) and stores it in the database with optional callback and threshold subscription support.
# telemetry.py - Monitoring ONOS port statistics and saving to database.
import threading
import time
import requests
from requests.auth import HTTPBasicAuth
from compiler.db import db
from compiler.onos_deployer import get_device_id_and_port_by_ip

ONOS_REST = "http://127.0.0.1:8181/onos/v1"
ONOS_USER = "onos"
ONOS_PASS = "rocks"

def get_port_statistics(device_id, port):
    url = f"{ONOS_REST}/statistics/ports/{device_id}/{port}"
    try:
        resp = requests.get(url, auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS), timeout=5)
        if resp.status_code != 200:
            return None
        return resp.json()['statistics'][0]['ports'][0]
    except Exception as e:
        print(f"[ONOS] Error getting port stats: {e}")
        return None

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
                "ip": ip,
                "timestamp": now,
                "rx_bps": 0,
                "tx_bps": 0,
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
            state.update({"rx_bytes": rx, "tx_bytes": tx, "time": now})
            if save_history:
                db.save_telemetry(ip, device_id, port, stats)
            if callback:
                callback(info)
            time.sleep(interval)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t

def calculate_loss(prev_stats, curr_stats):
    tx_prev = prev_stats.get('packetsSent', 0)
    tx_curr = curr_stats.get('packetsSent', 0)
    rx_prev = prev_stats.get('packetsReceived', 0)
    rx_curr = curr_stats.get('packetsReceived', 0)
    tx_delta = tx_curr - tx_prev
    rx_delta = rx_curr - rx_prev
    if tx_delta <= 0:
        return 0.0
    loss = (tx_delta - rx_delta) / tx_delta
    return max(0.0, min(1.0, loss))

def monitor_loss(ip, callback=None, interval=5):
    try:
        device_id, port = get_device_id_and_port_by_ip(ip)
    except Exception as e:
        print(f"[LossMonitor] Cannot monitor {ip}: {e}")
        return None

    state = {"prev_stats": None}
    loss_history = []

    def _run():
        nonlocal loss_history
        while True:
            stats = get_port_statistics(device_id, port)
            if not stats:
                time.sleep(interval)
                continue
            if state["prev_stats"] is not None:
                tx_delta = stats.get('packetsSent', 0) - state["prev_stats"].get('packetsSent', 0)
                if tx_delta > 200:
                    loss = calculate_loss(state["prev_stats"], stats)
                    loss_pct = loss * 100
                    loss_history.append(loss_pct)
                    if len(loss_history) > 3:
                        loss_history.pop(0)
                    avg_loss = sum(loss_history) / len(loss_history)
                    if callback:
                        callback(ip, avg_loss, stats)
                else:
                    if loss_history:
                        loss_history = []
            state["prev_stats"] = stats
            time.sleep(interval)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t

def get_last_ping_metrics(limit=10):
    cursor = db.db.ping_metrics.find().sort("timestamp", -1).limit(limit)
    result = []
    for doc in cursor:
        result.append({
            "src_ip": doc.get("src_ip"),
            "dst_ip": doc.get("dst_ip"),
            "timestamp": doc.get("timestamp"),
            "avg_delay_ms": doc.get("avg_delay_ms"),
            "jitter_ms": doc.get("jitter_ms"),
            "loss_percent": doc.get("loss_percent"),
            "min_delay_ms": doc.get("min_delay_ms"),
            "max_delay_ms": doc.get("max_delay_ms")
        })
    return result

def get_last_iperf_tcp(limit=10):
    cursor = db.db.iperf_tcp.find().sort("timestamp", -1).limit(limit)
    result = []
    for doc in cursor:
        result.append({
            "src_ip": doc.get("src_ip"),
            "dst_ip": doc.get("dst_ip"),
            "timestamp": doc.get("timestamp"),
            "bw_mbps": doc.get("bw_mbps"),
            "retransmits": doc.get("retransmits")
        })
    return result

def get_last_iperf_udp(limit=10):
    cursor = db.db.iperf_udp.find().sort("timestamp", -1).limit(limit)
    result = []
    for doc in cursor:
        result.append({
            "src_ip": doc.get("src_ip"),
            "dst_ip": doc.get("dst_ip"),
            "timestamp": doc.get("timestamp"),
            "bw_mbps": doc.get("bw_mbps"),
            "jitter_ms": doc.get("jitter_ms"),
            "loss_percent": doc.get("loss_percent")
        })
    return result

def get_latest_metrics_per_pair():
    pipeline = [{"$group": {"_id": {"src": "$src_ip", "dst": "$dst_ip"}}}]
    pairs = list(db.db.ping_metrics.aggregate(pipeline))
    result = {}
    for p in pairs:
        src = p["_id"]["src"]
        dst = p["_id"]["dst"]
        key = f"{src}->{dst}"
        ping_doc = db.db.ping_metrics.find_one({"src_ip": src, "dst_ip": dst}, sort=[("timestamp", -1)])
        tcp_doc = db.db.iperf_tcp.find_one({"src_ip": src, "dst_ip": dst}, sort=[("timestamp", -1)])
        udp_doc = db.db.iperf_udp.find_one({"src_ip": src, "dst_ip": dst}, sort=[("timestamp", -1)])
        result[key] = {
            "ping": {
                "avg_delay_ms": ping_doc.get("avg_delay_ms") if ping_doc else None,
                "jitter_ms": ping_doc.get("jitter_ms") if ping_doc else None,
                "loss_percent": ping_doc.get("loss_percent") if ping_doc else None,
                "timestamp": ping_doc.get("timestamp") if ping_doc else None
            },
            "iperf_tcp": {
                "bw_mbps": tcp_doc.get("bw_mbps") if tcp_doc else None,
                "retransmits": tcp_doc.get("retransmits") if tcp_doc else None,
                "timestamp": tcp_doc.get("timestamp") if tcp_doc else None
            },
            "iperf_udp": {
                "bw_mbps": udp_doc.get("bw_mbps") if udp_doc else None,
                "jitter_ms": udp_doc.get("jitter_ms") if udp_doc else None,
                "loss_percent": udp_doc.get("loss_percent") if udp_doc else None,
                "timestamp": udp_doc.get("timestamp") if udp_doc else None
            }
        }
    return result
