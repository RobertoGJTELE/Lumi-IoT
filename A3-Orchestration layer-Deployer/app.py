#Flask web service exposing the deploy endpoint.
# app.py - Flask server that exposes the /deploy endpoint and initiates security and telemetry.

from __future__ import print_function
import json
import os
import time
import threading
from flask import Flask, make_response, request
from flask_cors import CORS

from compiler.compiler import handle_request
from compiler.telemetry_onos import monitor_loss, monitor_bandwidth
from compiler.telemetry_onos import (
    get_last_ping_metrics,
    get_last_iperf_tcp,
    get_last_iperf_udp,
    get_latest_metrics_per_pair
)
from compiler.topology import get_all_ips
from compiler.onos_deployer import OVERHEAD_FACTORS

app = Flask(__name__)
CORS(app)

LAST_LOSS = {}
LAST_BANDWIDTH = {}

def on_loss(ip, loss_pct, stats):
    LAST_LOSS[ip] = loss_pct

def on_bandwidth(info):
    LAST_BANDWIDTH[info['ip']] = {
        "rx_bps": info.get("rx_bps", 0),
        "tx_bps": info.get("tx_bps", 0),
        "timestamp": info.get("timestamp", time.time())
    }

def print_iperf_metrics():
    while True:
        try:
            tcp_list = get_last_iperf_tcp(limit=1)
            if tcp_list:
                tcp = tcp_list[0]
                print(f"[ACTIVE] iperf TCP {tcp['src_ip']}->{tcp['dst_ip']}: {tcp['bw_mbps']:.2f} Mbps (ts={tcp['timestamp']})")
            udp_list = get_last_iperf_udp(limit=1)
            if udp_list:
                udp = udp_list[0]
                print(f"[ACTIVE] iperf UDP {udp['src_ip']}->{udp['dst_ip']}: bw={udp['bw_mbps']:.2f}Mbps, jitter={udp['jitter_ms']}ms, loss={udp['loss_percent']}% (ts={udp['timestamp']})")
        except Exception as e:
            print(f"[ACTIVE] Error al leer MongoDB: {e}")
        time.sleep(30)

@app.route("/", methods=["GET"])
def home():
    return "Lumi Deployer APIs"

@app.route("/deploy", methods=["POST"])
def deploy():
    req = request.get_json(silent=True, force=True)
    print("Request:", json.dumps(req, indent=4))
    try:
        res = handle_request(req)
    except Exception as err:
        print(err)
        res = {"status": {'code': 404, 'details': 'Could not deploy intent.'}}
    res = json.dumps(res, indent=4)
    print("Response:", res)
    r = make_response(res)
    r.headers["Content-Type"] = "application/json"
    return r

@app.route("/status", methods=["GET"])
def status():
    return {"overhead_factors": OVERHEAD_FACTORS}

@app.route("/loss", methods=["GET"])
def get_loss():
    return {"last_loss": LAST_LOSS}

@app.route("/bandwidth", methods=["GET"])
def get_bandwidth():
    return {"last_bandwidth": LAST_BANDWIDTH}

@app.route("/metrics", methods=["GET"])
def get_metrics():
    return {
        "loss": LAST_LOSS,
        "bandwidth": LAST_BANDWIDTH,
        "timestamp": time.time()
    }

@app.route("/active/ping", methods=["GET"])
def active_ping():
    limit = request.args.get('limit', default=10, type=int)
    data = get_last_ping_metrics(limit)
    return {"ping_metrics": data}

@app.route("/active/iperf_tcp", methods=["GET"])
def active_iperf_tcp():
    limit = request.args.get('limit', default=10, type=int)
    data = get_last_iperf_tcp(limit)
    return {"iperf_tcp": data}

@app.route("/active/iperf_udp", methods=["GET"])
def active_iperf_udp():
    limit = request.args.get('limit', default=10, type=int)
    data = get_last_iperf_udp(limit)
    return {"iperf_udp": data}

@app.route("/active/latest", methods=["GET"])
def active_latest():
    data = get_latest_metrics_per_pair()
    return {"active_metrics": data}

@app.route("/metrics/full", methods=["GET"])
def metrics_full():
    active = get_latest_metrics_per_pair()
    return {
        "onos_loss": LAST_LOSS,
        "onos_bandwidth": LAST_BANDWIDTH,
        "active_metrics": active,
        "timestamp": time.time()
    }

if __name__ == "__main__":
    print("[Deployer] Iniciando...")
    known_ips = get_all_ips()
    if not known_ips:
        print("[ERROR] No se encontraron IPs")
        exit(1)

    for ip in known_ips:
        monitor_loss(ip, callback=on_loss, interval=5)

    for ip in known_ips:
        monitor_bandwidth(ip, callback=on_bandwidth, interval=10, save_history=True)
        print(f"[Telemetry] Monitoreo de ancho de banda ONOS iniciado para {ip}")

    print("[Telemetry] Iniciando monitor de iperf activo (TCP/UDP)...")
    t = threading.Thread(target=print_iperf_metrics, daemon=True)
    t.start()

    print(f"[Telemetry] Todos los monitores iniciados para {len(known_ips)} IPs")
    port = int(os.getenv("PORT", 5000))
    app.run(debug=False, port=port, host="0.0.0.0")
