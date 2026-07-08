#Declaration of the assurance module that periodically reconciles the observed ONOS flow state with the desired state from the database, reinstating missing flows and logging redundant ones.
# assurance.py - Assurance module: compares observed vs expected state and corrects missing flows.
import os, time, threading, requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
from collections import defaultdict
from .onos_deployer import (
    install_flow,
    create_or_update_queue,
    get_device_id_and_port_by_ip
)
from .db import AssuranceDB

ONOS_URL = "http://127.0.0.1:8181/onos/v1"
ONOS_USER = "onos"
ONOS_PASS = "rocks"
CYCLE_INTERVAL = 30
ABSENCE_THRESHOLD = int(os.getenv("ASSURANCE_ABSENCE_THRESHOLD", "2"))

db = AssuranceDB()
absence_counters = defaultdict(int)

def get_onos_flows(device_id):
    url = f"{ONOS_URL}/flows/{device_id}"
    resp = requests.get(url, auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS), timeout=5)
    if resp.status_code != 200:
        return []
    return resp.json().get('flows', [])

def filter_lumi_flows(flows):
    return [f for f in flows if f.get('priority') == 50000]

def normalize_flow(flow):
    crit = {c['type']: c for c in flow.get('selector', {}).get('criteria', [])}
    src_ip = crit.get('IPV4_SRC', {}).get('ip', '').split('/')[0]
    dst_ip = crit.get('IPV4_DST', {}).get('ip', '').split('/')[0]
    instructions = flow.get('treatment', {}).get('instructions', [])

    queue_id = None
    output_port = None
    for i in instructions:
        if i['type'] == 'QUEUE':
            queue_id = i.get('queueId')
        elif i['type'] == 'OUTPUT':
            port_val = i.get('port')
            if port_val is not None:
                output_port = int(port_val)

    if not instructions:
        action = 'deny'
    else:
        action = 'allow_qos' if queue_id else 'allow'

    return {
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "action": action,
        "device_id": flow['deviceId'],
        "flow_id": flow['id'],
        "output_port": output_port,
        "queue_id": queue_id
    }

def acquire_observed_state():
    observed = []
    try:
        devices = requests.get(
            f"{ONOS_URL}/devices",
            auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS), timeout=5
        ).json().get('devices', [])
        for dev in devices:
            flows = get_onos_flows(dev['id'])
            observed.extend([normalize_flow(f) for f in filter_lumi_flows(flows)])
    except Exception as e:
        print(f"[Assurance] Error: {e}")
    return observed

def get_desired_state():
    now = datetime.now()
    all_rules = db.get_expected_rules()
    active = []
    for rule in all_rules:
        start_str = rule.get("start")
        end_str = rule.get("end")
        if start_str and end_str:
            try:
                st = datetime.strptime(start_str, "%H:%M").time()
                et = datetime.strptime(end_str, "%H:%M").time()
                if not (st <= now.time() <= et):
                    continue
            except:
                pass
        active.append(rule)
    return active

def compare_and_correct(observed, desired):
    obs_set = [{
        "src_ip": o["src_ip"], "dst_ip": o["dst_ip"], "action": o["action"],
        "output_port": o.get("output_port"), "queue_id": o.get("queue_id")
    } for o in observed]

    missing = []
    for d in desired:
        key = (d["src_ip"], d["dst_ip"], d["action"], d.get("output_port"), d.get("queue_id"))
        found = any(
            o["src_ip"] == key[0] and o["dst_ip"] == key[1] and o["action"] == key[2] and
            o.get("output_port") == key[3] and o.get("queue_id") == key[4]
            for o in obs_set
        )
        rule_id = f"{key}"
        if not found:
            absence_counters[rule_id] += 1
            if absence_counters[rule_id] >= ABSENCE_THRESHOLD:
                missing.append(d)
                absence_counters[rule_id] = 0
        else:
            absence_counters[rule_id] = 0

    redundant = []
    for o in obs_set:
        found = any(
            d["src_ip"] == o["src_ip"] and d["dst_ip"] == o["dst_ip"] and d["action"] == o["action"] and
            d.get("output_port") == o.get("output_port") and d.get("queue_id") == o.get("queue_id")
            for d in desired
        )
        if not found:
            redundant.append(o)
    if redundant:
        print("[Assurance] Redundant flows detected!")
        db.log_audit("REDUNDANT_FLOWS", redundant)

    for miss in missing:
        print(f"[Assurance] Reinstalling missing rule: {miss}")
        try:
            dev, _ = get_device_id_and_port_by_ip(miss["src_ip"])
            _, dport = get_device_id_and_port_by_ip(miss["dst_ip"])
            if miss["action"] == "deny":
                install_flow(dev, miss["src_ip"], miss["dst_ip"], action='deny')
            elif miss["action"] == "allow":
                install_flow(dev, miss["src_ip"], miss["dst_ip"], action='allow', output_port=dport)
            elif miss["action"] == "allow_qos" and miss.get("queue_id"):
                if miss.get("rate_bps"):
                    create_or_update_queue(dev, dport, miss["queue_id"], miss["rate_bps"])
                install_flow(dev, miss["src_ip"], miss["dst_ip"], action='allow',
                             queue_id=miss.get("queue_id"), output_port=dport)
            db.log_audit("CORRECTION", {"rule": miss})
        except Exception as e:
            db.log_audit("CORRECTION_FAILED", {"error": str(e)})
    return len(missing)

def assurance_cycle():
    print("[Assurance] Starting cycle...")
    observed = acquire_observed_state()
    desired = get_desired_state()
    print(f"[Assurance] Observed: {len(observed)}, Expected: {len(desired)}")
    compare_and_correct(observed, desired)

def start_assurance():
    def loop():
        time.sleep(10)
        while True:
            assurance_cycle()
            time.sleep(CYCLE_INTERVAL)
    threading.Thread(target=loop, daemon=True).start()
    print("[Assurance] Module started.")
