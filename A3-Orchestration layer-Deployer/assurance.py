#Declaration of the assurance module that periodically reconciles the observed ONOS flow state with the desired state from the database, reinstating missing flows and logging redundant ones.
# assurance.py - Assurance module: compares observed vs expected state and corrects missing flows.
import os
import time
import threading
import requests
from requests.auth import HTTPBasicAuth
from collections import defaultdict

from .onos_deployer import (
    install_flow,
    get_device_id_and_port_by_ip,
    delete_previous_flow,
    create_or_update_queue
)

from .db import AssuranceDB


ONOS_URL = "http://127.0.0.1:8181/onos/v1"
ONOS_USER = "onos"
ONOS_PASS = "rocks"
CYCLE_INTERVAL = 30
LUMI_FLOW_PRIORITY = 65000
ABSENCE_THRESHOLD = int(
    os.getenv("ASSURANCE_ABSENCE_THRESHOLD", "2")
)

db = AssuranceDB()
absence_counters = defaultdict(int)


def get_onos_flows(device_id):
    try:
        response = requests.get(
            f"{ONOS_URL}/flows/{device_id}",
            auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
            timeout=5
        )

        if response.status_code != 200:
            print(
                f"[Assurance] ONOS returned "
                f"{response.status_code} for {device_id}"
            )
            return []

        return response.json().get("flows", [])

    except Exception as e:
        print(
            f"[Assurance] Error fetching flows "
            f"from {device_id}: {e}"
        )
        return []


def filter_lumi_flows(flows):
    return [
        flow
        for flow in flows
        if flow.get("priority") == LUMI_FLOW_PRIORITY
    ]


def normalize_flow(flow):
    criteria = (
        flow.get("selector", {})
        .get("criteria", [])
    )

    src_ip = next(
        (
            c["ip"].split("/")[0]
            for c in criteria
            if c.get("type") == "IPV4_SRC"
        ),
        None
    )

    dst_ip = next(
        (
            c["ip"].split("/")[0]
            for c in criteria
            if c.get("type") == "IPV4_DST"
        ),
        None
    )

    traffic_type = "IP"

    for criterion in criteria:
        criterion_type = criterion.get("type")

        if criterion_type == "ETH_TYPE":
            eth_type = criterion.get("ethType")

            if eth_type in ("0x0800", "2048", 2048):
                traffic_type = "IPv4"

        elif criterion_type == "IP_PROTO":
            protocol = criterion.get("protocol")

            if protocol in ("6", 6):
                traffic_type = "TCP"
            elif protocol in ("17", 17):
                traffic_type = "UDP"
            elif protocol in ("1", 1):
                traffic_type = "ICMP"

    instructions = (
        flow.get("treatment", {})
        .get("instructions", [])
    )

    queue_id = None
    output_port = None

    for instruction in instructions:
        instruction_type = instruction.get("type")

        if instruction_type == "QUEUE":
            queue_id = instruction.get("queueId")

        elif instruction_type == "OUTPUT":
            port = instruction.get("port")

            try:
                output_port = int(port)
            except (TypeError, ValueError):
                output_port = port

    if not instructions:
        action = "deny"
    elif queue_id is not None:
        action = "allow_qos"
    else:
        action = "allow"

    return {
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "traffic_type": traffic_type,
        "action": action,
        "device_id": flow.get("deviceId"),
        "flow_id": flow.get("id"),
        "output_port": output_port,
        "queue_id": queue_id
    }


def acquire_observed_state():
    observed = []

    try:
        response = requests.get(
            f"{ONOS_URL}/devices",
            auth=HTTPBasicAuth(ONOS_USER, ONOS_PASS),
            timeout=5
        )

        if response.status_code != 200:
            print(
                f"[Assurance] Error retrieving devices: "
                f"{response.status_code}"
            )
            return observed

        devices = response.json().get("devices", [])

        for device in devices:
            flows = get_onos_flows(device["id"])
            lumi_flows = filter_lumi_flows(flows)

            for flow in lumi_flows:
                observed.append(normalize_flow(flow))

    except Exception as e:
        print(
            f"[Assurance] Error acquiring observed state: {e}"
        )

    return observed


def get_desired_state():
    return db.get_expected_rules()


def build_rule_key(rule):
    return (
        rule.get("src_ip"),
        rule.get("dst_ip"),
        rule.get("traffic_type"),
        rule.get("action"),
        rule.get("output_port"),
        rule.get("queue_id")
    )


def get_counter_key(rule):
    return (
        rule.get("src_ip"),
        rule.get("dst_ip"),
        rule.get("traffic_type"),
        rule.get("action"),
        rule.get("output_port"),
        rule.get("queue_id")
    )


def reinstall_rule(rule):
    print(
        f"[Assurance] Regenerating rule: {rule}"
    )

    try:
        device_id, _ = get_device_id_and_port_by_ip(
            rule["src_ip"]
        )

        _, dst_port = get_device_id_and_port_by_ip(
            rule["dst_ip"]
        )

        delete_previous_flow(
            device_id,
            rule["src_ip"],
            rule["dst_ip"]
        )

        if rule["action"] == "deny":

            install_flow(
                device_id,
                rule["src_ip"],
                rule["dst_ip"],
                action="deny"
            )

        elif (
            rule["action"] == "allow_qos"
            and rule.get("queue_id") is not None
            and rule.get("rate_bps") is not None
        ):

            create_or_update_queue(
                device_id,
                dst_port,
                rule["queue_id"],
                rule["rate_bps"]
            )

            install_flow(
                device_id,
                rule["src_ip"],
                rule["dst_ip"],
                action="allow",
                queue_id=rule["queue_id"],
                output_port=dst_port
            )

        else:

            install_flow(
                device_id,
                rule["src_ip"],
                rule["dst_ip"],
                action="allow",
                output_port=dst_port
            )

        return True

    except Exception as e:

        print(
            f"[Assurance] Correction failed "
            f"for {rule}: {e}"
        )

        return False


def compare_and_correct(observed, desired):

    observed_set = {
        build_rule_key(rule)
        for rule in observed
    }

    desired_set = {
        build_rule_key(rule)
        for rule in desired
    }

    missing = []

    for expected_rule in desired:

        expected_key = build_rule_key(expected_rule)
        counter_key = get_counter_key(expected_rule)

        if expected_key not in observed_set:

            absence_counters[counter_key] += 1

            print(
                f"[Assurance] Expected rule absent "
                f"({absence_counters[counter_key]}/"
                f"{ABSENCE_THRESHOLD}): "
                f"{expected_rule}"
            )

            if (
                absence_counters[counter_key]
                >= ABSENCE_THRESHOLD
            ):

                missing.append(expected_rule)
                absence_counters[counter_key] = 0

        else:

            absence_counters[counter_key] = 0

    redundant = [
        rule
        for rule in observed_set
        if rule not in desired_set
    ]

    if redundant:
        print(
            f"[Assurance] Redundant flows detected: "
            f"{redundant}"
        )

        db.log_audit(
            "REDUNDANT_FLOWS",
            list(redundant)
        )

    for rule in missing:

        print(
            f"[Assurance] Divergence confirmed: "
            f"{rule}"
        )

        success = reinstall_rule(rule)

        if success:
            db.log_audit(
                "CORRECTION",
                {
                    "rule": rule,
                    "reason": "missing_or_modified"
                }
            )
        else:
            db.log_audit(
                "CORRECTION_FAILED",
                {
                    "rule": rule,
                    "reason": "missing_or_modified"
                }
            )

    return len(missing)


def assurance_cycle():

    print("[Assurance] Starting cycle...")

    observed = acquire_observed_state()
    desired = get_desired_state()

    print(
        f"[Assurance] Observed: {len(observed)}, "
        f"Desired: {len(desired)}"
    )

    corrected = compare_and_correct(
        observed,
        desired
    )

    print(
        f"[Assurance] Corrections performed: "
        f"{corrected}"
    )


def start_assurance():

    def loop():

        time.sleep(10)

        while True:

            try:
                assurance_cycle()

            except Exception as e:

                print(
                    f"[Assurance] Unexpected error "
                    f"in assurance cycle: {e}"
                )

            time.sleep(CYCLE_INTERVAL)

    threading.Thread(
        target=loop,
        daemon=True
    ).start()

    print(
        "[Assurance] Module started. "
        f"Cycle interval: {CYCLE_INTERVAL}s"
    )
