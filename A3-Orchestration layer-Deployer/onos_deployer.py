#Low-level module for interacting with the ONOS REST API and managing QoS queues in OVS.
# onos_deployer.py - Interaction with the OJOS REST API to install/delete flows and queues.
import requests
import json
import subprocess
import re
import math
from requests.auth import HTTPBasicAuth

ONOS_URL = "http://127.0.0.1:8181/onos/v1"
ONOS_USER = "onos"
ONOS_PASS = "rocks"

DEFAULT_DEVICE_ID = "of:1000000000000001"
DEFAULT_PORT = 1
CONTAINER = "mn-wifi"

IP_TO_QUEUE_UDP = {
    '10.0.0.1': 0, '10.0.0.2': 1, '10.0.0.3': 2,
    '10.0.0.4': 3, '10.0.0.5': 4, '10.0.0.6': 5,
}
IP_TO_QUEUE_TCP = {
    '10.0.0.1': 6, '10.0.0.2': 7, '10.0.0.3': 8,
    '10.0.0.4': 9, '10.0.0.5': 10, '10.0.0.6': 11,
}

STATIC_PORT_MAP = {
    '10.0.0.1': 1, '10.0.0.2': 1, '10.0.0.3': 1,
    '10.0.0.4': 1, '10.0.0.5': 1, '10.0.0.6': 1,
}

QUEUE_UUID_MAP = None
_OVS_PORT_CACHE = None
OVERHEAD_FACTORS = {}

def run_ovs_cmd(cmd_args, capture_output=False, check=False):
    full_cmd = ["docker", "exec", CONTAINER] + cmd_args
    print(f"[OVS-CMD] Running: {' '.join(full_cmd)}")
    if capture_output:
        result = subprocess.run(full_cmd, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise Exception(f"Command failed: {result.stderr}")
        return result
    else:
        subprocess.run(full_cmd, check=check)

def detect_ovs_port():
    global _OVS_PORT_CACHE
    if _OVS_PORT_CACHE:
        return _OVS_PORT_CACHE
    result = run_ovs_cmd(["ovs-vsctl", "list-ports", "ap1"], capture_output=True)
    if result.returncode == 0:
        for port in result.stdout.strip().split('\n'):
            if 'wlan' in port or 'eth' in port:
                _OVS_PORT_CACHE = port
                return port
    check = run_ovs_cmd(["ovs-vsctl", "list", "port", "ap1-wlan1"], capture_output=True)
    if check.returncode == 0:
        _OVS_PORT_CACHE = "ap1-wlan1"
        return "ap1-wlan1"
    raise Exception("Could not detect OVS port.")

def load_queue_uuids():
    print("[UUID] === Loading queue mapping from OVS ===")
    result = run_ovs_cmd(["ovs-vsctl", "list", "qos"], capture_output=True)
    if result.returncode != 0:
        print(f"[UUID] Error: {result.stderr}")
        return None

    for line in result.stdout.split('\n'):
        if 'queues' in line and '{' in line:
            match = re.search(r'queues\s*:\s*\{(.*)\}', line)
            if match:
                items = match.group(1).split(',')
                queue_map = {}
                for item in items:
                    item = item.strip()
                    if '=' in item:
                        key, value = item.split('=')
                        key = int(key.strip())
                        value = value.strip()
                        queue_map[key] = value
                print("[UUID] Mapping loaded:")
                for k, v in queue_map.items():
                    print(f"  Queue {k} -> {v}")
                return queue_map
            else:
                start = line.find('{')
                end = line.rfind('}')
                if start != -1 and end != -1:
                    rest = line[start+1:end]
                    items = rest.split(',')
                    queue_map = {}
                    for item in items:
                        item = item.strip()
                        if '=' in item:
                            key, value = item.split('=')
                            key = int(key.strip())
                            value = value.strip()
                            queue_map[key] = value
                    print("[UUID] Mapping loaded (fallback)")
                    return queue_map
    print("[UUID] QoS not found or format is incorrect")
    return None

def get_queue_uuid(qid):
    global QUEUE_UUID_MAP
    if QUEUE_UUID_MAP is None:
        QUEUE_UUID_MAP = load_queue_uuids()
        if QUEUE_UUID_MAP is None:
            return None
    uuid = QUEUE_UUID_MAP.get(qid)
    if uuid:
        print(f"[UUID] Queue {qid} -> {uuid}")
    else:
        print(f"[UUID] No UUID found for queue {qid}")
    return uuid

def update_queue_rate(ip, rate_bps, proto=None):
    print(f"[QoS] update_queue_rate: ip={ip}, rate_bps={rate_bps}, proto={proto}")
    rate_mbps = rate_bps / 1_000_000

    if proto == 17:
        burst_time_ms = max(20, min(200, 200 / (rate_mbps / 10 + 0.1)))
        overhead = 1.05 + 0.01 * 0.5
        adjusted_rate = int(rate_bps * overhead)
        burst = int((adjusted_rate / 8) * (burst_time_ms / 1000))
        burst = max(10000, min(5_000_000, burst))

    elif proto == 6:
        overhead = 1.15
        adjusted_rate = int(rate_bps * overhead)
        burst = int(adjusted_rate / 8 * 0.15)
        burst = max(burst, 15000)
        burst = min(burst, 10_000_000)
        burst_time_ms = 0

    else:
        overhead = 1.10
        burst_time_ms = 100
        adjusted_rate = int(rate_bps * overhead)
        burst = int((adjusted_rate / 8) * (burst_time_ms / 1000))
        burst = max(10000, min(5_000_000, burst))

    print(f"[QoS] Parameters: overhead={overhead:.2f}, burst_time={burst_time_ms:.0f}ms")
    print(f"[QoS] max-rate={adjusted_rate} bps ({adjusted_rate/1_000_000:.1f} Mbps), burst={burst} bytes ({burst/1024:.1f} KB)")

    if proto == 17:
        qid = IP_TO_QUEUE_UDP.get(ip)
    elif proto == 6:
        qid = IP_TO_QUEUE_TCP.get(ip)
    else:
        qid = None

    if qid is None:
        print(f"[QoS] ERROR: No queue defined for {ip} with proto {proto}")
        return

    uuid = get_queue_uuid(qid)
    if not uuid:
        print(f"[QoS] CRITICAL ERROR: No UUID found for queue {qid}.")
        print("[QoS] Please run the queue creation command manually.")
        return

    cmd = ["ovs-vsctl", "set", "queue", uuid,
           f"other-config:max-rate={adjusted_rate}",
           f"other-config:burst={burst}"]
    result = run_ovs_cmd(cmd, capture_output=True)
    if result.returncode != 0:
        print(f"[QoS] Error updating queue {qid}: {result.stderr}")
    else:
        print(f"[QoS] Queue {qid} updated successfully")

def get_device_id_and_port_by_ip(ip):
    try:
        resp = requests.get(
            f"{ONOS_URL}/hosts",
            auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
            headers={"Accept": "application/json"},
            timeout=3
        )
        if resp.status_code == 200:
            for host in resp.json().get('hosts', []):
                if ip in host.get('ipAddresses', []):
                    loc = host['locations'][0]
                    return loc['elementId'], int(loc['port'])
    except Exception:
        pass
    return DEFAULT_DEVICE_ID, STATIC_PORT_MAP.get(ip, DEFAULT_PORT)

def delete_all_deployer_flows(device_id, src_ip, dst_ip):
    print(f"[DEL] Removing deployer rules for {src_ip} <-> {dst_ip}")
    try:
        resp = requests.get(
            f"{ONOS_URL}/flows/{device_id}",
            auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
            headers={"Accept": "application/json"},
            timeout=3
        )
        if resp.status_code != 200:
            return
        for flow in resp.json().get('flows', []):
            if flow.get('priority') != 65000:
                continue
            sel = flow.get('selector', {}).get('criteria', [])
            has_src = any(c.get('type') == 'IPV4_SRC' and c.get('ip') == f"{src_ip}/32" for c in sel)
            has_dst = any(c.get('type') == 'IPV4_DST' and c.get('ip') == f"{dst_ip}/32" for c in sel)
            if has_src or has_dst:
                flow_id = flow.get('id')
                if flow_id:
                    del_resp = requests.delete(
                        f"{ONOS_URL}/flows/{device_id}/{flow_id}",
                        auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
                        timeout=3
                    )
                    if del_resp.status_code == 204:
                        print(f"[DEL] Deleted flow {flow_id}")
                    else:
                        print(f"[DEL] Error deleting flow {flow_id}: {del_resp.status_code}")
    except Exception as e:
        print(f"[DEL] Error: {e}")

def install_flow(device_id, ip, queue_id=None, direction="src", action="allow", proto=None):
    priority = 65000
    criteria = [
        {"type": "ETH_TYPE", "ethType": "0x0800"},
        {"type": "IN_PORT", "port": "1"}
    ]
    if proto is not None:
        criteria.append({"type": "IP_PROTO", "protocol": proto})
    if direction == "src":
        criteria.append({"type": "IPV4_SRC", "ip": f"{ip}/32"})
    else:
        criteria.append({"type": "IPV4_DST", "ip": f"{ip}/32"})

    flow = {
        "priority": priority,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": device_id,
        "selector": {"criteria": criteria}
    }

    if action == "deny":
        flow["treatment"] = {"instructions": []}
    else:
        instructions = []
        if queue_id is not None:
            instructions.append({"type": "QUEUE", "queueId": queue_id})
        instructions.append({"type": "OUTPUT", "port": "IN_PORT"})
        flow["treatment"] = {"instructions": instructions}

    print(f"[INST] Sending to ONOS: {json.dumps(flow, indent=2)}")
    resp = requests.post(
        f"{ONOS_URL}/flows/{device_id}",
        auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
        data=json.dumps(flow),
        headers={"Content-Type": "application/json"},
        timeout=3
    )
    if resp.status_code >= 400:
        raise Exception(f"ONOS error {resp.status_code}: {resp.text[:200]}")
    proto_name = {1: "ICMP", 6: "TCP", 17: "UDP"}.get(proto, "IP")
    queue_info = f"queue={queue_id}" if queue_id is not None else "no QoS"
    print(f"[INST] Rule {direction} {proto_name} for {ip} {queue_info}")

def install_generic_flow(device_id, priority=50000):
    flow = {
        "priority": priority,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": device_id,
        "selector": {
            "criteria": [
                {"type": "ETH_TYPE", "ethType": "0x0800"}
            ]
        },
        "treatment": {
            "instructions": [
                {"type": "OUTPUT", "port": "IN_PORT"}
            ]
        }
    }
    resp = requests.post(
        f"{ONOS_URL}/flows/{device_id}",
        auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
        data=json.dumps(flow),
        headers={"Content-Type": "application/json"},
        timeout=3
    )
    if resp.status_code >= 400:
        print(f"[INST] Error installing general rule: {resp.text[:200]}")
    else:
        print("[INST] General forwarding rule installed")
