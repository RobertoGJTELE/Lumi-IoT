
"""
Active telemetry module for Mininet-WiFi.

IMPORTANT: This script MUST be executed INSIDE the Mininet-WiFi container
(e.g., the container running the topology, often named "mn-wifi" or similar).
It uses mnexec to enter the network namespaces of the Mininet hosts,
so it requires direct access to the Mininet process and host PIDs.
If run outside the container, mnexec will not find the host namespaces.

MongoDB is expected to run externally (e.g., on the WSL host) and is
accessed via the MONGO_URI environment variable.
"""
#!/usr/bin/env python3
import threading
import time
import subprocess
import re
import pymongo
import os

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://172.29.36.54:27017/lumi")

class MongoClient:
    def __init__(self, uri=MONGO_URI):
        self.client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client.get_database()

    def save_ping_metric(self, src_ip, dst_ip, data):
        doc = {
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "timestamp": time.time(),
            "avg_delay_ms": data.get("avg_delay_ms"),
            "jitter_ms": data.get("jitter_ms"),
            "loss_percent": data.get("loss_percent"),
            "min_delay_ms": data.get("min_delay_ms"),
            "max_delay_ms": data.get("max_delay_ms"),
            "packets_sent": data.get("packets_sent"),
            "packets_received": data.get("packets_received")
        }
        self.db.ping_metrics.insert_one(doc)

    def save_iperf_tcp_metric(self, src_ip, dst_ip, data):
        doc = {
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "timestamp": time.time(),
            "bw_mbps": data.get("bw_mbps", 0),
            "retransmits": data.get("retransmits", 0)
        }
        self.db.iperf_tcp.insert_one(doc)

    def save_iperf_udp_metric(self, src_ip, dst_ip, data):
        doc = {
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "timestamp": time.time(),
            "bw_mbps": data.get("bw_mbps", 0),
            "jitter_ms": data.get("jitter_ms", 0),
            "loss_percent": data.get("loss_percent", 0)
        }
        self.db.iperf_udp.insert_one(doc)

db = MongoClient()

def host_cmd(host, command, timeout=30):
    if not hasattr(host, 'pid') or host.pid is None:
        try:
            pid = int(subprocess.check_output(['pgrep', '-f', f'bash.*{host.name}'], text=True).strip().split()[0])
            host.pid = pid
        except:
            raise RuntimeError(f"No se pudo obtener PID para {host.name}")
    full_cmd = ['mnexec', '-a', str(host.pid), 'bash', '-c', command]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return f"TIMEOUT: {command}"
    except Exception as e:
        return f"ERROR: {e}"

def measure_ping(src_host, dst_ip, count=20):
    cmd = f"ping -c {count} -i 0.1 {dst_ip}"
    output = host_cmd(src_host, cmd)
    avg = jitter = min_t = max_t = loss = None
    match = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms', output)
    if match:
        min_t = float(match.group(1))
        avg = float(match.group(2))
        max_t = float(match.group(3))
        jitter = float(match.group(4))
    else:
        times = re.findall(r'time[<=]\s*([\d.]+)\s*ms', output)
        if times:
            times = [float(t) for t in times]
            avg = sum(times) / len(times)
            min_t = min(times)
            max_t = max(times)
            if len(times) > 1:
                jitter = (sum((t - avg) ** 2 for t in times) / (len(times) - 1)) ** 0.5
            else:
                jitter = 0.0
    loss_match = re.search(r'([\d.]+)% packet loss', output)
    if loss_match:
        loss = float(loss_match.group(1))
    else:
        loss = 0.0
    sent = count
    recv = sent - int(sent * loss / 100) if loss else sent
    return {
        'avg_delay_ms': avg,
        'jitter_ms': jitter,
        'loss_percent': loss,
        'min_delay_ms': min_t,
        'max_delay_ms': max_t,
        'packets_sent': sent,
        'packets_received': recv
    }

def measure_iperf_tcp(src_host, dst_host, duration=10, port=5003):
    if not host_cmd(src_host, 'which iperf').strip():
        return {'bw_mbps': 0, 'retransmits': 0}
    if not host_cmd(dst_host, 'which iperf').strip():
        return {'bw_mbps': 0, 'retransmits': 0}
    host_cmd(dst_host, f'pkill iperf; iperf -s -p {port} -D')
    time.sleep(1)
    cmd = f"iperf -c {dst_host.IP()} -p {port} -t {duration} -f m"
    output = host_cmd(src_host, cmd, timeout=15)
    bw = 0.0
    match = re.search(r'([\d.]+)\s+Mbits/sec', output)
    if match:
        bw = float(match.group(1))
    host_cmd(dst_host, 'pkill iperf')
    return {'bw_mbps': bw, 'retransmits': 0}

def measure_iperf_udp(src_host, dst_host, duration=10, bitrate='10M', port=5004):
    if not host_cmd(src_host, 'which iperf').strip():
        return {'jitter_ms': 0, 'loss_percent': 0, 'bw_mbps': 0}
    if not host_cmd(dst_host, 'which iperf').strip():
        return {'jitter_ms': 0, 'loss_percent': 0, 'bw_mbps': 0}
    host_cmd(dst_host, f'pkill iperf; iperf -s -u -p {port} -D')
    time.sleep(1)
    cmd = f"iperf -c {dst_host.IP()} -u -b {bitrate} -t {duration} -p {port} -f m"
    output = host_cmd(src_host, cmd, timeout=15)
    jitter = 0.0
    loss = 0.0
    bw = 0.0
    match = re.search(r'([\d.]+)\s+ms\s+(\d+)/\s*(\d+)\s*\(([\d.]+)%\)', output)
    if match:
        jitter = float(match.group(1))
        lost = int(match.group(2))
        total = int(match.group(3))
        loss = float(match.group(4))
    else:
        match = re.search(r'([\d.]+)\s+ms\s+(\d+)/\s*(\d+)', output)
        if match:
            jitter = float(match.group(1))
            lost = int(match.group(2))
            total = int(match.group(3))
            if total > 0:
                loss = (lost / total) * 100
    bw_match = re.search(r'([\d.]+)\s+Mbits/sec', output)
    if bw_match:
        bw = float(bw_match.group(1))
    host_cmd(dst_host, 'pkill iperf')
    return {'jitter_ms': jitter, 'loss_percent': loss, 'bw_mbps': bw}

def measure_all_pairs(hosts, pairs, interval=60, verbose=False):
    def _worker():
        while True:
            for src, dst in pairs:
                src_ip = src.IP()
                dst_ip = dst.IP()
                ping_res = measure_ping(src, dst_ip)
                db.save_ping_metric(src_ip, dst_ip, ping_res)
                if verbose:
                    print(f"[Telemetry] Ping {src_ip}->{dst_ip}: avg={ping_res['avg_delay_ms']}ms, jitter={ping_res['jitter_ms']}ms, loss={ping_res['loss_percent']}%")
                if int(time.time() / interval) % 2 == 0:
                    tcp_res = measure_iperf_tcp(src, dst)
                    db.save_iperf_tcp_metric(src_ip, dst_ip, tcp_res)
                    if verbose:
                        print(f"[Telemetry] iperf TCP {src_ip}->{dst_ip}: {tcp_res['bw_mbps']:.2f} Mbps")
                udp_res = measure_iperf_udp(src, dst)
                db.save_iperf_udp_metric(src_ip, dst_ip, udp_res)
                if verbose:
                    print(f"[Telemetry] iperf UDP {src_ip}->{dst_ip}: jitter={udp_res['jitter_ms']}ms, loss={udp_res['loss_percent']}%, bw={udp_res['bw_mbps']:.2f} Mbps")
            time.sleep(interval)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t
