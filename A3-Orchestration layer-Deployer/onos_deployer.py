#Low-level module for interacting with the ONOS REST API and managing QoS queues in OVS.
# onos_deployer.py - Interaction with the OJOS REST API to install/delete flows and queues.
import requests, json, subprocess, hashlib
from requests.auth import HTTPBasicAuth

ONOS_URL = "http://127.0.0.1:8181/onos/v1"
ONOS_USER = "onos"
ONOS_PASS = "rocks"

STATIC_PORT_MAP = {
    '10.0.0.1': 1,
    '10.0.0.2': 2,
    '10.0.0.3': 3,
    '10.0.0.4': 4,
    '10.0.0.5': 5,
    '10.0.0.6': 6,
}

def get_switch_number_from_dpid(device_id):
    return int(device_id.split(':')[-1], 16)

def get_ovs_port_name(device_id, of_port):
    switch_num = get_switch_number_from_dpid(device_id)
    return f"s{switch_num}-eth{of_port}"

def get_device_id_and_port_by_ip(ip):
    try:
        resp = requests.get(
            f"{ONOS_URL}/hosts",
            auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
            headers={"Accept": "application/json"},
            timeout=5
        )
        if resp.status_code == 200:
            hosts = resp.json().get('hosts', [])
            for host in hosts:
                if ip in host.get('ipAddresses', []):
                    loc = host['locations'][0]
                    return loc['elementId'], loc['port']
    except Exception:
        pass

    if ip in STATIC_PORT_MAP:
        return "of:0000000000000001", STATIC_PORT_MAP[ip]
    raise Exception(f"Could not find location for IP {ip}")

def delete_previous_flow(device_id, src_ip, dst_ip):
    try:
        resp = requests.get(
            f"{ONOS_URL}/flows/{device_id}",
            auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
            headers={"Accept": "application/json"},
            timeout=5
        )
        if resp.status_code != 200:
            return
        flows = resp.json().get('flows', [])
        for flow in flows:
            selector = flow.get('selector', {})
            criteria = selector.get('criteria', [])
            src_match = any(c.get('type') == 'IPV4_SRC' and c.get('ip') == f"{src_ip}/32" for c in criteria)
            dst_match = any(c.get('type') == 'IPV4_DST' and c.get('ip') == f"{dst_ip}/32" for c in criteria)
            if src_match and dst_match:
                flow_id = flow.get('id')
                if flow_id:
                    del_resp = requests.delete(
                        f"{ONOS_URL}/flows/{device_id}/{flow_id}",
                        auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
                        timeout=5
                    )
                    if del_resp.status_code == 204:
                        print(f"Deleted previous flow {flow_id} ({src_ip}->{dst_ip})")
    except Exception as e:
        print(f"Error deleting flow: {e}")

def create_or_update_queue(device_id, port_number, queue_id, rate_bps):
    ovs_port = get_ovs_port_name(device_id, port_number)
    burst_seconds = 0.01
    overhead_factor = 1.028
    adjusted_rate = int(rate_bps * overhead_factor)
    burst_bytes = int((adjusted_rate / 8) * burst_seconds)
    burst_bytes = max(burst_bytes, 1500)
    burst_bytes = min(burst_bytes, 10 * 1024 * 1024)

    subprocess.run(["sudo", "ovs-vsctl", "clear", "port", ovs_port, "qos"], check=False)

    queue_cmd = [
        "sudo", "ovs-vsctl", "create", "queue",
        f"other-config:min-rate={adjusted_rate}",
        f"other-config:max-rate={adjusted_rate}",
        f"other-config:burst={burst_bytes}"
    ]
    queue_result = subprocess.run(queue_cmd, capture_output=True, text=True, check=True)
    queue_uuid = queue_result.stdout.strip()

    qos_cmd = [
        "sudo", "ovs-vsctl", "create", "qos", "type=linux-htb",
        f"queues:{queue_id}={queue_uuid}"
    ]
    qos_result = subprocess.run(qos_cmd, capture_output=True, text=True, check=True)
    qos_uuid = qos_result.stdout.strip()

    subprocess.run(
        ["sudo", "ovs-vsctl", "set", "port", ovs_port, f"qos={qos_uuid}"],
        check=True, capture_output=True, text=True
    )
    print(f"Queue {queue_id} created on {ovs_port} with rate {adjusted_rate} bps, burst {burst_bytes} bytes")

def install_flow(device_id, src_ip, dst_ip, action="deny", queue_id=None, output_port=None):
    flow = {
        "priority": 50000,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": device_id,
        "selector": {
            "criteria": [
                {"type": "ETH_TYPE", "ethType": "0x0800"},
                {"type": "IPV4_SRC", "ip": f"{src_ip}/32"},
                {"type": "IPV4_DST", "ip": f"{dst_ip}/32"}
            ]
        }
    }
    if action == "deny":
        flow["treatment"] = {"instructions": []}
    else:
        instructions = []
        if queue_id is not None:
            instructions.append({"type": "QUEUE", "queueId": queue_id})
        if output_port is None:
            raise ValueError("output_port must be provided for allow action")
        instructions.append({"type": "OUTPUT", "port": str(output_port)})
        flow["treatment"] = {"instructions": instructions}

    headers = {"Content-Type": "application/json"}
    resp = requests.post(
        f"{ONOS_URL}/flows/{device_id}",
        auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
        data=json.dumps(flow),
        headers=headers,
        timeout=5
    )
    if resp.status_code >= 400:
        raise Exception(f"ONOS flow error {resp.status_code}: {resp.text[:500]}")
    print(f"Flow installed on {device_id} ({action} {src_ip}→{dst_ip})")

