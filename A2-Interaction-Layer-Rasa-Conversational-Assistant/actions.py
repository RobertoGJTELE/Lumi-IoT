#Declaration of the custom Rasa actions for building, deploying, managing feedback, confirming deployment, canceling sessions, and responding to robotics law queries.
import json, os, re, traceback, requests
from typing import Any, Text, Dict, List, Optional
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet

from .parser import parse_entities
from .response import make_simple_response, make_card_response
from .beautifier import beautify_intent_colored

from nile.builder import build, build_nile_fallback
from nile.exceptions import MissingTargetError, MissingOperationError

# Connect to MongoDB if available.
try:
    from database.client import Database
    db = Database()
    print("[Webhook] MongoDB connected.")
except Exception as e:
    print(f"[Webhook] MongoDB error: {e}")
    db = None

# Deployer endpoint.
DEPLOY_URL = os.getenv("DEPLOY_URL", "http://192.168.56.10:5000/deploy")

def _format_nice_intent(nile: str) -> str:
    # Pretty-format a Nile intent for display.
    parts = {}
    match = re.search(r"define intent (\w+Intent)", nile)
    parts["name"] = match.group(1) if match else "Nile Intent"
    targets = re.findall(r"(\w+)\('([^']+)'\)", nile)
    if targets:
        parts["targets"] = [f"{t[0]}: {t[1]}" for t in targets]
    for op in ["monitor", "set", "unset", "allow", "block", "add", "remove"]:
        if op in nile:
            parts["operation"] = op
            break
    thresh = re.search(r"threshold\('([^']+)',\s*'([^']+)'\)", nile)
    if thresh:
        parts["threshold"] = f"{thresh.group(1)} {thresh.group(2)}"
    time_range = re.search(r"start hour\('([^']+)'\) end hour\('([^']+)'\)", nile)
    if time_range:
        parts["time"] = f"{time_range.group(1)} - {time_range.group(2)}"
    lines = [f"**Intent:** {parts['name']}"]
    if "targets" in parts:
        lines.append("**Targets:**")
        for t in parts["targets"]:
            lines.append(f"  - {t}")
    if "operation" in parts:
        lines.append(f"**Action:** {parts['operation']}")
    if "threshold" in parts:
        lines.append(f"**Threshold:** {parts['threshold']}")
    if "time" in parts:
        lines.append(f"**Time:** {parts['time']}")
    beautiful_nile = beautify_intent_colored(nile)
    lines.append("\n**Raw Nile:**")
    lines.append(beautiful_nile)
    return "\n".join(lines)

def _deploy_nile(nile_cmd, disp):
    # Send Nile command to deployer.
    try:
        r = requests.post(DEPLOY_URL, json={"intent": nile_cmd}, timeout=30)
        if r.status_code == 200:
            msg = r.json().get("status", {}).get("details", "OK")
            disp.utter_message(text=f"Deployer: {msg}")
            return True
        else:
            disp.utter_message(text=f"Deployer HTTP {r.status_code}")
            return False
    except Exception as e:
        disp.utter_message(text=f"Deployment error: {e}")
        return False

def _extract_entities_manually(text: str) -> List[Dict[str, str]]:
    # Manual entity extraction fallback.
    entities = []
    text_lower = text.lower().strip()
    has_operation = False

    print(f"[MANUAL] Procesando texto: '{text}'")

    # Detect operation.
    if re.search(r'\b(block|deny|prevent)\b', text_lower):
        entities.append({"entity": "operation", "value": "block"})
        has_operation = True
        print("[MANUAL] Detectada operación: block")
    elif re.search(r'\b(allow|permit|unblock)\b', text_lower):
        entities.append({"entity": "operation", "value": "allow"})
        has_operation = True
        print("[MANUAL] Detectada operación: allow")
    elif re.search(r'\b(simulate)\b', text_lower):
        entities.append({"entity": "operation", "value": "simulate"})
        has_operation = True
        print("[MANUAL] Detectada operación: simulate")
    elif re.search(r'\b(predict)\b', text_lower):
        entities.append({"entity": "operation", "value": "predict"})
        has_operation = True
        print("[MANUAL] Detectada operación: predict")
    elif re.search(r'\b(add|remove)\b', text_lower):
        if not any(e.get("entity") == "operation" for e in entities):
            entities.append({"entity": "operation", "value": "add"})
            has_operation = True
            print("[MANUAL] Detectada operación: add/remove")
    elif re.search(r'\b(shutdown|reboot|reset|power off)\b', text_lower):
        entities.append({"entity": "operation", "value": "add"})
        has_operation = True
        print("[MANUAL] Detectada operación: power action")
    elif re.search(r'\b(get|show|monitor|check|measure|read|fetch|display)\b', text_lower):
        entities.append({"entity": "operation", "value": "monitor"})
        has_operation = True
        print("[MANUAL] Detectada operación: monitor")
    elif re.search(r'\b(set|configure|apply|limit|define|allocate|schedule)\b', text_lower):
        entities.append({"entity": "operation", "value": "set"})
        has_operation = True
        print("[MANUAL] Detectada operación: set")

    # Duty cycle.
    if re.search(r'\bduty cycle\b', text_lower):
        dc_match = re.search(r'(\d+)%', text_lower)
        if dc_match:
            entities.append({"entity": "duty_cycle", "value": f"{dc_match.group(1)}%"})
            if not has_operation:
                entities.append({"entity": "operation", "value": "set"})
                has_operation = True
            entities = [e for e in entities if e.get("entity") != "qos_value"]

    # Power saving.
    if re.search(r'\b(power saving|energy saving)\b', text_lower):
        if re.search(r'\b(on|enable)\b', text_lower):
            entities.append({"entity": "power_saving_mode", "value": "on"})
        elif re.search(r'\b(off|disable)\b', text_lower):
            entities.append({"entity": "power_saving_mode", "value": "off"})
        else:
            entities.append({"entity": "power_saving_mode", "value": "on"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True

    # QoS / bandwidth.
    if not any(e.get("entity") == "duty_cycle" for e in entities):
        qos_match = re.search(r'\b(\d+[KMG]?bps?)\b', text_lower)
        if qos_match:
            entities.append({"entity": "qos_value", "value": qos_match.group(1)})
            if not has_operation:
                entities.append({"entity": "operation", "value": "set"})
                has_operation = True
        bw_match = re.search(r'\b(bandwidth|rate|limit)\s+to\s+(\d+[KMG]?bps?)\b', text_lower)
        if bw_match:
            entities.append({"entity": "qos_value", "value": bw_match.group(2)})
            if not has_operation:
                entities.append({"entity": "operation", "value": "set"})
                has_operation = True

    # Security actions.
    if re.search(r'\b(secure join|add node|remove node|authenticate)\b', text_lower):
        if 'secure join' in text_lower:
            entities.append({"entity": "security_action", "value": "secure_join"})
        elif 'authenticate' in text_lower:
            entities.append({"entity": "security_action", "value": "authenticate"})
        elif 'add node' in text_lower:
            entities.append({"entity": "security_action", "value": "add"})
        elif 'remove node' in text_lower:
            entities.append({"entity": "security_action", "value": "remove"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "add"})
            has_operation = True

    # Simulation / prediction helpers.
    if any(e.get("entity") == "operation" and e.get("value") in ["simulate", "predict"] for e in entities):
        pred_match = re.search(r'predict\s+(.+?)\s+(?:of|for)\s+', text_lower)
        if pred_match:
            pred_target = pred_match.group(1).strip().replace(' ', '_')
            entities.append({"entity": "query_type", "value": pred_target})
        duration_match = re.search(r'\b(\d+)\s*(hours|minutes|days)\b', text_lower)
        if duration_match:
            entities.append({"entity": "simulation_duration", "value": f"{duration_match.group(1)} {duration_match.group(2)}"})

    # Node patterns.
    node_pattern = r'\b(temp\d|hum\d|air\d|cam\d*|cam|10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'
    explicit_node = re.search(r'\bnode\s+(' + node_pattern + r')\b', text_lower)
    if explicit_node:
        val = explicit_node.group(1)
        if val and val not in ["node", "all"]:
            if not any(e.get("entity") == "node" and e.get("value") == val for e in entities):
                entities.append({"entity": "node", "value": val})

    node_matches = re.findall(node_pattern, text_lower)
    for nm in node_matches:
        if nm and nm not in ["block", "allow", "get", "set", "limit", "from", "to", "in", "at", "between", "node", "all"]:
            if not any(e.get("entity") == "node" and e.get("value") == nm for e in entities):
                entities.append({"entity": "node", "value": nm})

    # Node groups.
    group_matches = re.findall(r'\b(all|sensors|gateways|actuators|routers|leaves|dodag|root)\b', text_lower)
    if group_matches:
        entities.append({"entity": "node_group", "value": group_matches[-1]})

    # Origin.
    origin_pattern = r'\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|temp\d|hum\d|air\d|cam\d*|cam)\b'
    from_match = re.search(r'\bfrom\s+(' + origin_pattern + r')\b', text_lower)
    if from_match:
        entities.append({"entity": "origin", "value": from_match.group(1)})
    else:
        words = text_lower.split()
        for i, word in enumerate(words):
            if re.match(origin_pattern, word) and word not in ["block", "allow", "get", "set", "limit", "from", "to", "in", "at", "between"]:
                if i == 0 or (i > 0 and words[i-1] in ["between", "and"]):
                    entities.append({"entity": "origin", "value": word})
                    break

    # Destination.
    to_match = re.search(r'\bto\s+(' + origin_pattern + r')\b', text_lower)
    if to_match:
        entities.append({"entity": "destination", "value": to_match.group(1)})
    else:
        words = text_lower.split()
        for i, word in enumerate(words):
            if re.match(origin_pattern, word) and word not in ["block", "allow", "get", "set", "limit", "from", "to", "in", "at", "between"]:
                if i == 1 or (i > 0 and words[i-1] in ["and", "to"]):
                    if not any(e.get("entity") == "destination" for e in entities):
                        entities.append({"entity": "destination", "value": word})
                        break

    # Protocol.
    proto_match = re.search(r'\b(tcp|udp|http|https|ftp|ssh|sftp|dns|snmp)\b', text_lower)
    if proto_match:
        entities.append({"entity": "protocol", "value": proto_match.group(1)})

    # Location.
    loc_match = re.search(r'\b(in|at|for)\s+([a-zA-Z0-9_ ]+?)(?:\s+for|\s+to|\s+from|\s+$|\s+and)', text_lower)
    if loc_match:
        loc_value = loc_match.group(2).strip()
        if loc_value not in ["the", "a", "my", "endpoint", "traffic", "bandwidth"]:
            entities.append({"entity": "location", "value": loc_value})

    # Sensor types.
    sensor_types = [
        'temperature', 'humidity', 'motion', 'light', 'brightness', 'pressure',
        'gas', 'smoke', 'proximity', 'sound', 'noise', 'uv', 'ultraviolet',
        'co2', 'pm2.5', 'pm10', 'voc', 'no2', 'ozone', 'air quality',
        'flame', 'rain', 'wind speed', 'soil moisture', 'vibration',
        'leak', 'radiation', 'water level', 'voltage', 'current', 'power',
        'flow', 'speed', 'ph', 'turbidity', 'door', 'window', 'occupancy',
        'flood', 'presence', 'level'
    ]
    for sensor in sensor_types:
        if re.search(r'\b' + re.escape(sensor) + r'\b', text_lower):
            entities.append({"entity": "sensor_type", "value": sensor})
            break

    # Air quality params.
    air_params = ['co2', 'carbon dioxide', 'pm2.5', 'pm10', 'voc', 'volatile organic', 'no2', 'ozone', 'air quality index']
    for param in air_params:
        if re.search(r'\b' + re.escape(param) + r'\b', text_lower):
            if not any(e.get("entity") == "sensor_type" and e.get("value") in air_params for e in entities):
                entities.append({"entity": "air_quality_parameter", "value": param})
                break

    # Objective function.
    of_match = re.search(r'\b(OF0|MRHOF|COM-OF|ADAPTIVE-OF|ADDITIVE-OF|EEQ|EL-RPL|RM-RPL|CQAOF|SIGMA)\b', text_lower, re.IGNORECASE)
    if of_match:
        entities.append({"entity": "objective_function", "value": of_match.group(1).upper()})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True

    # Trickle algorithm.
    trickle_match = re.search(r'\b(FL-Trickle|FI-Trickle|D-Trickle|Elastic Trickle|LA-Trickle|EE-Trickle)\b', text_lower, re.IGNORECASE)
    if trickle_match:
        entities.append({"entity": "trickle_algorithm", "value": trickle_match.group(1)})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True

    # Query type.
    query_match = re.search(r'\b(energy consumption|battery level|link quality|routing table|topology|neighbors|dodag)\b', text_lower)
    if query_match:
        entities.append({"entity": "query_type", "value": query_match.group(1).replace(' ', '_')})
        if not has_operation:
            entities.append({"entity": "operation", "value": "monitor"})
            has_operation = True

    # Energy metric.
    metric_match = re.search(r'\b(ETX|energy|hop_count|RSSI|residual_energy|buffer_occupancy)\b', text_lower, re.IGNORECASE)
    if metric_match:
        entities.append({"entity": "energy_metric", "value": metric_match.group(1).lower()})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True

    # Routing protocol.
    routing_protocols = ['rpl', 'dymo-low', 'load', 'hydro', 'thread', '6lowpan', '6tisch']
    for proto in routing_protocols:
        if re.search(r'\b' + re.escape(proto) + r'\b', text_lower):
            entities.append({"entity": "routing_protocol", "value": proto.upper()})
            if not has_operation:
                entities.append({"entity": "operation", "value": "set"})
                has_operation = True
            break

    # Power actions.
    power_actions = ['shutdown', 'reboot', 'reset', 'power off', 'power_off']
    for action in power_actions:
        if re.search(r'\b' + re.escape(action) + r'\b', text_lower):
            if action == 'power off':
                entities.append({"entity": "power_action", "value": "power_off"})
            else:
                entities.append({"entity": "power_action", "value": action})
            if not has_operation:
                entities.append({"entity": "operation", "value": "add"})
                has_operation = True
            break

    # Schedule hours.
    has_update_schedule = any(e.get("entity") == "update_schedule" for e in entities)
    if not has_update_schedule:
        hour_pattern = r'\b(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]\b'
        hours = re.findall(hour_pattern, text_lower)
        if len(hours) >= 2:
            entities.append({"entity": "start", "value": hours[0]})
            entities.append({"entity": "end", "value": hours[1]})
        elif len(hours) == 1:
            if re.search(r'\b(to|until|end|at)\b', text_lower):
                entities.append({"entity": "end", "value": hours[0]})
            elif re.search(r'\b(from|start|begin)\b', text_lower):
                entities.append({"entity": "start", "value": hours[0]})
            else:
                entities.append({"entity": "end", "value": hours[0]})

    # Sleep mode.
    sleep_match = re.search(r'\b(sleep schedule|wake interval|sleep time|deep sleep|light sleep|idle)\b', text_lower)
    if sleep_match:
        if 'deep sleep' in text_lower:
            entities.append({"entity": "sleep_mode", "value": "deep_sleep"})
        elif 'light sleep' in text_lower:
            entities.append({"entity": "sleep_mode", "value": "light_sleep"})
        elif 'idle' in text_lower:
            entities.append({"entity": "sleep_mode", "value": "idle"})
        elif 'sleep schedule' in text_lower:
            entities.append({"entity": "sleep_mode", "value": "deep_sleep"})
        wake_match = re.search(r'\b(\d+)\s*(seconds|minutes|hours)\b', text_lower)
        if wake_match:
            entities.append({"entity": "wake_interval", "value": f"{wake_match.group(1)} {wake_match.group(2)}"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True

    # Trickle timing.
    trickle_imin = re.search(r'\b(\d+)\s*(ms|s)\b', text_lower)
    if trickle_imin and 'imin' in text_lower:
        entities.append({"entity": "trickle_imin", "value": trickle_imin.group(0)})
    trickle_imax = re.search(r'\b(\d+)\s*(ms|s)\b', text_lower)
    if trickle_imax and 'imax' in text_lower:
        entities.append({"entity": "trickle_imax", "value": trickle_imax.group(0)})
    redundancy_match = re.search(r'\bredundancy\s+to\s+(\d+)\b', text_lower)
    if redundancy_match:
        entities.append({"entity": "trickle_redundancy", "value": redundancy_match.group(1)})

    # Radio settings.
    if re.search(r'\bfrequency hopping\b', text_lower):
        if re.search(r'\b(on|enable)\b', text_lower):
            entities.append({"entity": "frequency_hopping", "value": "on"})
        elif re.search(r'\b(off|disable)\b', text_lower):
            entities.append({"entity": "frequency_hopping", "value": "off"})
        else:
            entities.append({"entity": "frequency_hopping", "value": "on"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True
    channel_match = re.search(r'\b(channel|channel page)\s+to\s+(\d+)\b', text_lower)
    if channel_match:
        entities.append({"entity": "radio_channel", "value": channel_match.group(2)})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True
    tx_match = re.search(r'\b(tx power|transmission power)\s+to\s+(\d+)\s*(dBm|dB)\b', text_lower)
    if tx_match:
        entities.append({"entity": "tx_power", "value": f"{tx_match.group(2)} {tx_match.group(3)}"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True
    data_rate_match = re.search(r'\b(data rate)\s+to\s+(\d+)\s*(kbps|Mbps)\b', text_lower)
    if data_rate_match:
        entities.append({"entity": "data_rate", "value": f"{data_rate_match.group(2)} {data_rate_match.group(3)}"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True

    # Alerts and reports.
    alert_match = re.search(r'\b(alert|notify)\s+when\s+(battery below|link quality drops|node offline)\b', text_lower)
    if alert_match:
        entities.append({"entity": "alert_condition", "value": alert_match.group(2).replace(' ', '_')})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True
    report_match = re.search(r'\b(every|each)\s+(\d+)\s*(hours|minutes|days)\b', text_lower)
    if report_match:
        entities.append({"entity": "report_interval", "value": f"{report_match.group(2)} {report_match.group(3)}"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True

    # Firmware.
    if re.search(r'\bfirmware version\b', text_lower):
        ver_match = re.search(r'\b(v?\d+\.\d+\.\d+)\b', text_lower)
        if ver_match:
            entities.append({"entity": "firmware_version", "value": ver_match.group(1)})
        else:
            entities.append({"entity": "firmware_version", "value": "unknown"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "monitor"})
            has_operation = True
    elif re.search(r'\b(update firmware|ota update|schedule.*update)\b', text_lower):
        entities.append({"entity": "update_schedule", "value": "now"})
        time_match = re.search(r'\bat\s+(\d{2}:\d{2})\b', text_lower)
        if time_match:
            entities.append({"entity": "update_schedule", "value": time_match.group(1)})
        if not has_operation:
            entities.append({"entity": "operation", "value": "set"})
            has_operation = True
    elif re.search(r'\brollback firmware\b', text_lower):
        entities.append({"entity": "rollback", "value": "true"})
        if not has_operation:
            entities.append({"entity": "operation", "value": "add"})
            has_operation = True

    print(f"[MANUAL] Entidades extraídas: {entities}")
    return entities

def _normalize_operation(op: str, entities: List[Dict[str, str]] = None) -> str:
    # Normalize operation names.
    op_lower = op.lower()
    has_security = any(e.get("entity") == "security_action" for e in entities) if entities else False
    has_power = any(e.get("entity") == "power_action" for e in entities) if entities else False

    if has_security or has_power:
        if op_lower in ["turn_on", "turn_off", "enable", "disable", "add", "remove"]:
            return "add"

    if op_lower in ["turn_on", "turn_off", "enable", "disable", "set", "configure", "apply", "limit", "define", "allocate", "schedule"]:
        return "set"
    if op_lower in ["add", "remove", "install", "dismiss", "include", "authenticate", "balance", "prefer", "avoid", "prioritize", "reduce"]:
        return "add"
    if op_lower in ["shutdown", "reboot", "reset", "power_off"]:
        return "add"
    if op_lower in ["get", "show", "list", "display", "fetch", "report", "monitor", "check", "measure", "read"]:
        return "monitor"
    if op_lower in ["simulate", "predict", "what if", "compare"]:
        if op_lower == "predict":
            return "predict"
        return "simulate"
    if op_lower in ["block", "deny", "prevent"]:
        return "block"
    if op_lower in ["allow", "permit", "unblock"]:
        return "allow"
    return op

def _check_required_entities(entities, text=""):
    # Validate required entities for the normalized operation.
    text_lower = text.lower() if text else ""
    if re.search(r'\b(block|deny|prevent)\b', text_lower):
        if not any(e.get("entity") == "operation" for e in entities):
            entities.append({"entity": "operation", "value": "block"})
            print("[CHECK] Forzada operación block por texto")
    elif re.search(r'\b(simulate)\b', text_lower):
        if not any(e.get("entity") == "operation" for e in entities):
            entities.append({"entity": "operation", "value": "simulate"})
            print("[CHECK] Forzada operación simulate por texto")

    has_origin = any(e.get("entity") == "origin" for e in entities)
    has_destination = any(e.get("entity") == "destination" for e in entities)
    has_qos = any(e.get("entity") == "qos_value" for e in entities)
    has_location = any(e.get("entity") == "location" and e.get("value") not in ["network", "any", "default"] for e in entities)
    has_sensor = any(e.get("entity") == "sensor_type" for e in entities)
    has_air = any(e.get("entity") == "air_quality_parameter" for e in entities)
    has_middlebox = any(e.get("entity") == "middlebox" for e in entities)
    has_node = any(e.get("entity") == "node" and e.get("value") not in ["node", "all"] for e in entities)
    has_node_group = any(e.get("entity") == "node_group" and e.get("value") not in ["all"] for e in entities)
    has_power_action = any(e.get("entity") == "power_action" for e in entities)
    has_power_saving = any(e.get("entity") == "power_saving_mode" for e in entities)
    has_of = any(e.get("entity") == "objective_function" for e in entities)
    has_dc = any(e.get("entity") == "duty_cycle" for e in entities)
    has_trickle = any(e.get("entity") == "trickle_algorithm" for e in entities)
    has_metric = any(e.get("entity") == "energy_metric" for e in entities)
    has_routing = any(e.get("entity") == "routing_protocol" for e in entities)
    has_query = any(e.get("entity") == "query_type" for e in entities)
    has_start = any(e.get("entity") == "start" for e in entities)
    has_end = any(e.get("entity") == "end" for e in entities)
    has_security_action = any(e.get("entity") == "security_action" for e in entities)
    has_simulation_duration = any(e.get("entity") == "simulation_duration" for e in entities)
    has_radio_channel = any(e.get("entity") == "radio_channel" for e in entities)
    has_tx_power = any(e.get("entity") == "tx_power" for e in entities)
    has_data_rate = any(e.get("entity") == "data_rate" for e in entities)
    has_frequency_hopping = any(e.get("entity") == "frequency_hopping" for e in entities)
    has_alert_condition = any(e.get("entity") == "alert_condition" for e in entities)
    has_report_interval = any(e.get("entity") == "report_interval" for e in entities)
    has_firmware_version = any(e.get("entity") == "firmware_version" for e in entities)
    has_update_schedule = any(e.get("entity") == "update_schedule" for e in entities)
    has_rollback = any(e.get("entity") == "rollback" for e in entities)
    has_sleep_mode = any(e.get("entity") == "sleep_mode" for e in entities)
    has_wake_interval = any(e.get("entity") == "wake_interval" for e in entities)

    print(f"[CHECK] Entidades recibidas: {entities}")
    print(f"[CHECK] has_power_saving: {has_power_saving}, has_node: {has_node}, has_node_group: {has_node_group}")
    print(f"[CHECK] has_power_action: {has_power_action}, has_query: {has_query}")
    print(f"[CHECK] has_security_action: {has_security_action}")
    print(f"[CHECK] has_update_schedule: {has_update_schedule}")
    print(f"[CHECK] has_duty_cycle: {has_dc}")
    print(f"[CHECK] has_rollback: {has_rollback}")

    operation = None
    for e in entities:
        if e.get("entity") == "operation":
            operation = e.get("value")
            break

    if not operation:
        if re.search(r'\b(simulate)\b', text_lower):
            operation = "simulate"
            entities.append({"entity": "operation", "value": "simulate"})
        elif re.search(r'\b(predict)\b', text_lower):
            operation = "predict"
            entities.append({"entity": "operation", "value": "predict"})
        elif re.search(r'\b(block|deny|prevent)\b', text_lower):
            operation = "block"
            entities.append({"entity": "operation", "value": "block"})
        elif re.search(r'\b(allow|permit|unblock)\b', text_lower):
            operation = "allow"
            entities.append({"entity": "operation", "value": "allow"})
        elif re.search(r'\b(get|show|monitor|check|measure|read|fetch|display)\b', text_lower):
            operation = "monitor"
            entities.append({"entity": "operation", "value": "monitor"})
        elif re.search(r'\b(set|configure|apply|limit|define|allocate|schedule)\b', text_lower):
            operation = "set"
            entities.append({"entity": "operation", "value": "set"})
        elif re.search(r'\b(add|remove|install|dismiss|include|authenticate|balance|prefer|avoid|prioritize|reduce)\b', text_lower):
            operation = "add"
            entities.append({"entity": "operation", "value": "add"})
        else:
            if has_origin or has_destination:
                operation = "block"
                entities.append({"entity": "operation", "value": "block"})
            elif has_sensor or has_air:
                operation = "monitor"
                entities.append({"entity": "operation", "value": "monitor"})
            elif has_qos:
                operation = "set"
                entities.append({"entity": "operation", "value": "set"})
            elif has_power_saving or has_of or has_dc or has_trickle or has_metric or has_routing or has_radio_channel or has_tx_power or has_data_rate or has_frequency_hopping:
                operation = "set"
                entities.append({"entity": "operation", "value": "set"})
            elif has_power_action:
                operation = "add"
                entities.append({"entity": "operation", "value": "add"})
            elif has_security_action:
                operation = "add"
                entities.append({"entity": "operation", "value": "add"})
            elif has_sleep_mode or has_wake_interval:
                operation = "set"
                entities.append({"entity": "operation", "value": "set"})
            elif has_simulation_duration:
                operation = "simulate"
                entities.append({"entity": "operation", "value": "simulate"})
            else:
                return {"msg": "What operation do you want to perform? (block, allow, get, set, add, remove)", "missing": "operation"}

    normalized_op = _normalize_operation(operation, entities)
    print(f"[CHECK] Operación original: {operation} -> Normalizada: {normalized_op}")

    if normalized_op in ["block", "allow"]:
        if not has_origin and not has_destination:
            print("[CHECK] Falta origen y destino -> mensaje exacto")
            return {"msg": "What are the source and destination?", "missing": "origin_destination"}
        if not has_origin:
            return {"msg": "I need the source. Please provide it like: 'from <origin>'", "missing": "origin"}
        if not has_destination:
            return {"msg": "I need the destination. Please provide it like: 'to <destination>'", "missing": "destination"}
        return None

    if normalized_op == "predict":
        if not has_query:
            return {"msg": "What do you want to predict? (e.g., 'battery life')", "missing": "query_type"}
        return None

    if normalized_op == "monitor":
        if has_firmware_version:
            return None
        if has_query:
            if not has_node and not has_node_group:
                print("[CHECK] query sin nodo/grupo -> feedback")
                return {"msg": "Which node or group do you want to monitor?", "missing": "target_node"}
            return None
        if not has_sensor and not has_air:
            return {"msg": "What sensor type or air quality parameter do you want? (e.g., 'temperature', 'humidity', 'CO2')", "missing": "sensor"}
        if not has_location:
            return {"msg": "What is the location? (e.g., 'lab', 'server room')", "missing": "location"}
        return None

    if normalized_op == "set":
        if has_power_saving and not has_node and not has_node_group:
            print("[CHECK] power_saving sin nodo/grupo -> feedback")
            return {"msg": "Which node or group do you want to apply this action to? (e.g., 'node temp1', 'all sensors')", "missing": "target_node"}

        if not has_update_schedule:
            if has_start and not has_end:
                return {"msg": "I need the end time for the schedule (e.g., 'to 12:00')", "missing": "end"}
            if has_end and not has_start:
                return {"msg": "I need the start time for the schedule (e.g., 'from 08:00')", "missing": "start"}

        if (has_qos or has_of or has_dc or has_trickle or has_metric or has_routing or
            has_radio_channel or has_tx_power or has_data_rate or has_frequency_hopping or
            has_sleep_mode or has_wake_interval or has_power_saving or has_update_schedule):
            return None

        if has_node or has_node_group:
            return {"msg": "What do you want to set for this node/group? (e.g., 'power saving on', 'objective function OF0')", "missing": "energy_config"}

        return {"msg": "What do you want to set? (e.g., 'power saving on', 'objective function OF0', 'duty cycle 10%', 'routing protocol RPL')", "missing": "energy_config"}

    if normalized_op == "add":
        if has_rollback:
            if has_node or has_node_group:
                return None
            else:
                return {"msg": "Which node or group do you want to rollback firmware on?", "missing": "target_node"}

        if has_security_action:
            real_node = any(e.get("entity") == "node" and e.get("value") not in ["node", "all"] for e in entities)
            real_group = any(e.get("entity") == "node_group" and e.get("value") not in ["all"] for e in entities)
            if not real_node and not real_group:
                print("[CHECK] security_action sin nodo/grupo real -> feedback")
                return {"msg": "Which node or group do you want to apply this security action to?", "missing": "target_node"}
            return None

        if has_power_action:
            real_node = any(e.get("entity") == "node" and e.get("value") not in ["node", "all"] for e in entities)
            real_group = any(e.get("entity") == "node_group" and e.get("value") not in ["all"] for e in entities)
            if not real_node and not real_group:
                print("[CHECK] power_action sin nodo/grupo real -> feedback")
                return {"msg": "Which node or group do you want to apply this action to? (e.g., 'temp1', '10.0.0.1', 'all sensors')", "missing": "target_node"}
            return None

        if has_middlebox:
            return None

        return {"msg": "What do you want to add or remove? (e.g., 'middlebox firewall', 'node 10.0.0.5')", "missing": "add_remove_target"}

    if normalized_op == "simulate":
        if has_query:
            return None
        if not has_simulation_duration:
            print("[CHECK] Falta duración -> mensaje exacto")
            return {"msg": "For how long do you want to simulate?", "missing": "simulation_duration"}
        return None

    return None

def _build_nile_fallback(entities, text=None):
    # Build Nile command from entities when main builder fails.
    if text and re.search(r'\bduty cycle\b', text.lower()):
        dc_match = re.search(r'(\d+)%', text.lower())
        if dc_match:
            duty_cycle_value = f"{dc_match.group(1)}%"
            entities = [e for e in entities if e.get("entity") != "qos_value"]
            if not any(e.get("entity") == "duty_cycle" for e in entities):
                entities.append({"entity": "duty_cycle", "value": duty_cycle_value})
            if not any(e.get("entity") == "operation" for e in entities):
                entities.append({"entity": "operation", "value": "set"})

    origin = destination = qos_value = protocol = operation = None
    power_saving = objective_function = duty_cycle = trickle_algorithm = energy_metric = routing_protocol = power_action = None
    node = node_group = start = end = query_type = metric_to_show = sleep_mode = wake_interval = security_action = None
    trickle_imin = trickle_imax = trickle_redundancy = radio_channel = tx_power = data_rate = frequency_hopping = None
    simulation_duration = alert_condition = report_interval = firmware_version = update_schedule = rollback = None
    location = None

    for e in entities:
        entity_type = e.get("entity")
        value = e.get("value")
        if entity_type == "origin": origin = value
        elif entity_type == "destination": destination = value
        elif entity_type == "qos_value": qos_value = value
        elif entity_type == "protocol": protocol = value
        elif entity_type == "operation": operation = value
        elif entity_type == "power_saving_mode": power_saving = value
        elif entity_type == "objective_function": objective_function = value
        elif entity_type == "duty_cycle": duty_cycle = value
        elif entity_type == "trickle_algorithm": trickle_algorithm = value
        elif entity_type == "energy_metric": energy_metric = value
        elif entity_type == "routing_protocol": routing_protocol = value
        elif entity_type == "power_action": power_action = value
        elif entity_type == "node": node = value
        elif entity_type == "node_group": node_group = value
        elif entity_type == "start": start = value
        elif entity_type == "end": end = value
        elif entity_type == "query_type": query_type = value
        elif entity_type == "metric_to_show": metric_to_show = value
        elif entity_type == "sleep_mode": sleep_mode = value
        elif entity_type == "wake_interval": wake_interval = value
        elif entity_type == "security_action": security_action = value
        elif entity_type == "trickle_imin": trickle_imin = value
        elif entity_type == "trickle_imax": trickle_imax = value
        elif entity_type == "trickle_redundancy": trickle_redundancy = value
        elif entity_type == "radio_channel": radio_channel = value
        elif entity_type == "tx_power": tx_power = value
        elif entity_type == "data_rate": data_rate = value
        elif entity_type == "frequency_hopping": frequency_hopping = value
        elif entity_type == "simulation_duration": simulation_duration = value
        elif entity_type == "alert_condition": alert_condition = value
        elif entity_type == "report_interval": report_interval = value
        elif entity_type == "firmware_version": firmware_version = value
        elif entity_type == "update_schedule": update_schedule = value
        elif entity_type == "rollback": rollback = value
        elif entity_type == "location": location = value

    target = ""
    if node: target = f" node('{node}')"
    elif node_group: target = f" group('{node_group}')"

    schedule = ""
    if start and end: schedule = f" start hour('{start}') end hour('{end}')"
    elif start: schedule = f" start hour('{start}')"
    elif end: schedule = f" end hour('{end}')"

    if operation == "predict":
        if query_type and node:
            return f"predict {query_type} for node('{node}'){schedule}"
        elif query_type:
            return f"predict {query_type}{schedule}"
        elif node:
            return f"predict for node('{node}'){schedule}"
        else:
            return f"predict{schedule}"

    if operation == "simulate":
        if query_type and node:
            return f"predict {query_type} for node('{node}'){schedule}"
        elif query_type:
            return f"predict {query_type}{schedule}"
        elif simulation_duration:
            return f"simulate for {simulation_duration}{target}{schedule}"
        else:
            return f"simulate{target}{schedule}"

    if query_type:
        if target:
            return f"get {query_type} for{target}"
        else:
            return f"get {query_type}"

    if power_action:
        if power_action == "shutdown":
            return f"shutdown{target}{schedule}" if target else f"shutdown all nodes{schedule}"
        elif power_action == "reboot":
            return f"reboot{target}{schedule}" if target else f"reboot all nodes{schedule}"
        elif power_action == "reset":
            return f"reset{target}{schedule}" if target else f"reset all nodes{schedule}"
        elif power_action == "power_off":
            return f"power_off{target}{schedule}" if target else f"power_off all nodes{schedule}"

    if power_saving:
        return f"set power_saving('{power_saving}'){target}{schedule}"

    if objective_function:
        return f"set objective_function('{objective_function}'){target}{schedule}"

    if duty_cycle:
        return f"set duty_cycle('{duty_cycle}'){target}{schedule}"

    if trickle_algorithm:
        return f"set trickle_algorithm('{trickle_algorithm}'){target}{schedule}"

    if energy_metric:
        return f"set energy_metric('{energy_metric}'){target}{schedule}"

    if routing_protocol:
        return f"set routing_protocol('{routing_protocol}'){target}{schedule}"

    if sleep_mode or wake_interval:
        cmd = ""
        if sleep_mode: cmd += f"set sleep_mode('{sleep_mode}')"
        if wake_interval: cmd += f"set wake_interval('{wake_interval}')"
        return f"{cmd}{target}{schedule}".lstrip()

    if security_action:
        if node: return f"security_action('{security_action}') for node('{node}'){schedule}"
        elif node_group: return f"security_action('{security_action}') for group('{node_group}'){schedule}"
        else: return f"security_action('{security_action}'){schedule}"

    trickle_cmd = ""
    if trickle_imin: trickle_cmd += f"set trickle_imin('{trickle_imin}')"
    if trickle_imax: trickle_cmd += f"set trickle_imax('{trickle_imax}')"
    if trickle_redundancy: trickle_cmd += f"set trickle_redundancy('{trickle_redundancy}')"
    if trickle_cmd:
        return f"{trickle_cmd}{target}{schedule}".lstrip()

    radio_cmd = ""
    if radio_channel: radio_cmd += f"set radio_channel('{radio_channel}')"
    if tx_power: radio_cmd += f"set tx_power('{tx_power}')"
    if data_rate: radio_cmd += f"set data_rate('{data_rate}')"
    if frequency_hopping: radio_cmd += f"set frequency_hopping('{frequency_hopping}')"
    if radio_cmd:
        return f"{radio_cmd}{target}{schedule}".lstrip()

    if alert_condition: return f"set alert('{alert_condition}'){target}"
    if report_interval: return f"set report_interval('{report_interval}'){target}"

    if firmware_version: return f"set firmware_version('{firmware_version}'){target}"
    if update_schedule: return f"schedule firmware_update at '{update_schedule}'{target}"
    if rollback: return f"rollback firmware{target}"

    if qos_value and not duty_cycle:
        return f"set bandwidth('{qos_value}')"

    if operation in ["block", "allow"]:
        if origin and destination:
            return f"{operation} traffic from endpoint('{origin}') to endpoint('{destination}')"
        elif origin:
            return f"{operation} traffic from endpoint('{origin}')"
        elif destination:
            return f"{operation} traffic to endpoint('{destination}')"
        else:
            return f"{operation} traffic"

    if operation == "monitor":
        sensor = next((e.get("value") for e in entities if e.get("entity") in ["sensor_type", "air_quality_parameter"]), None)
        if sensor and location:
            return f"monitor {sensor} in {location}"
        elif sensor:
            return f"monitor {sensor}"
        elif location:
            return f"monitor in {location}"

    return "allow traffic"

class ActionBuild(Action):
    def name(self): return "action_build"

    def run(self, disp, tracker, domain):
        # Main build action.
        if tracker.get_slot("awaiting_feedback"):
            return self._handle_as_feedback(disp, tracker)

        try:
            text = tracker.latest_message.get("text", "")
            rasa_entities = tracker.latest_message.get("entities", [])
            manual_entities = _extract_entities_manually(text)
            combined = list(rasa_entities)
            for ent in manual_entities:
                if not any(e.get("entity") == ent["entity"] and e.get("value") == ent["value"] for e in combined):
                    combined.append(ent)

            text_lower = text.lower()
            if not any(e.get("entity") == "operation" for e in combined):
                if re.search(r'\b(block|deny|prevent)\b', text_lower):
                    combined.append({"entity": "operation", "value": "block"})
                    print("[ACTION] Forzada operación block en ActionBuild")
                elif re.search(r'\b(simulate)\b', text_lower):
                    combined.append({"entity": "operation", "value": "simulate"})
                    print("[ACTION] Forzada operación simulate en ActionBuild")
                elif re.search(r'\b(predict)\b', text_lower):
                    combined.append({"entity": "operation", "value": "predict"})
                    print("[ACTION] Forzada operación predict en ActionBuild")

            entities = combined

            print(f"[DEBUG] ActionBuild - Texto: {text}")
            print(f"[DEBUG] ActionBuild - Entidades combinadas: {entities}")

            if not entities:
                make_simple_response(disp, "I didn't understand that. Could you rephrase?")
                return []

            missing = _check_required_entities(entities, text)
            if missing:
                print(f"[DEBUG] Missing detectado: {missing}")
                if missing.get("missing") in ["origin_destination", "simulation_duration"]:
                    make_simple_response(disp, missing["msg"])
                else:
                    make_simple_response(disp, "I need more information to build the rule.")
                    make_simple_response(disp, missing["msg"])
                return [
                    SlotSet("awaiting_feedback", True),
                    SlotSet("original_entities", entities),
                    SlotSet("pending_message", text),
                    SlotSet("missing_entity_type", missing.get("missing")),
                    SlotSet("entity", None),
                    SlotSet("value", None),
                ]

            parsed = parse_entities(entities)
            try:
                nile = _build_nile_fallback(entities, text)
            except Exception as e:
                print(f"[Builder] Error en fallback: {e}")
                nile = "allow traffic"

            if db:
                db.insert_intent(tracker.sender_id, text, entities, nile)

            nice_text = _format_nice_intent(nile)
            make_card_response(disp, "Nile Intent", "Generated rule",
                               "Is this what you want?", nice_text,
                               suggestions=["Yes", "No"])

            return [
                SlotSet("pending_nile_command", nile),
                SlotSet("pending_confirmation", True),
                SlotSet("original_entities", entities),
                SlotSet("parsed_entities", parsed),
                SlotSet("intent_id", "lumi_intent"),
                SlotSet("entity", None),
                SlotSet("value", None),
                SlotSet("missing_entity_type", None),
                SlotSet("missing_entity_value", None),
                SlotSet("awaiting_feedback", False),
            ]
        except Exception as e:
            traceback.print_exc()
            make_simple_response(disp, f"Error: {e}")
            return []

    def _handle_as_feedback(self, disp, tracker):
        # Handle user feedback when required entities are missing.
        cancel_keywords = [
            "cancel", "cancelar", "start over", "abort", "stop", "never mind",
            "forget it", "quit", "exit", "end", "terminate", "don't continue"
        ]
        last_intent = tracker.latest_message.get("intent", {}).get("name")
        last_text = tracker.latest_message.get("text", "").lower()

        if last_intent == "cancel" or any(word in last_text for word in cancel_keywords):
            disp.utter_message(text="Okay. Please start over then.")
            return [
                SlotSet("entity", None), SlotSet("value", None),
                SlotSet("missing_entity_type", None), SlotSet("missing_entity_value", None),
                SlotSet("awaiting_feedback", False),
                SlotSet("pending_confirmation", False), SlotSet("pending_nile_command", None),
                SlotSet("parsed_entities", None), SlotSet("original_entities", None),
                SlotSet("pending_message", None),
            ]

        missing_type = tracker.get_slot("missing_entity_type")
        if not missing_type:
            disp.utter_message(text="Let's start over.")
            return [
                SlotSet("awaiting_feedback", False),
                SlotSet("original_entities", None),
                SlotSet("pending_message", None),
                SlotSet("missing_entity_type", None),
                SlotSet("entity", None),
                SlotSet("value", None),
                SlotSet("pending_confirmation", False),
                SlotSet("pending_nile_command", None),
                SlotSet("parsed_entities", None),
            ]

        user_text = tracker.latest_message.get("text", "")
        original = tracker.get_slot("original_entities") or []

        entity_map = {
            "target_node": "node",
            "energy_config": "objective_function",
            "sensor": "sensor_type",
            "location": "location",
            "origin": "origin",
            "destination": "destination",
            "simulation_duration": "simulation_duration",
            "query_type": "query_type",
            "add_remove_target": "node"
        }
        entity_type = entity_map.get(missing_type, missing_type)

        updated = list(original)

        if missing_type == "target_node":
            node_match = re.search(r'\b(temp\d|hum\d|air\d|cam\d*|cam|10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b', user_text)
            if node_match:
                updated.append({"entity": "node", "value": node_match.group(1)})
            elif "all sensors" in user_text.lower():
                updated.append({"entity": "node_group", "value": "sensors"})
            elif "gateways" in user_text.lower():
                updated.append({"entity": "node_group", "value": "gateways"})
            else:
                disp.utter_message(text="I need a valid node name (e.g., 'temp1', '10.0.0.1') or 'all sensors'.")
                return [SlotSet("awaiting_feedback", True), SlotSet("missing_entity_type", missing_type)]
        elif missing_type == "origin":
            origin_match = re.search(r'(?:from\s+)?(\S+)', user_text)
            if origin_match:
                origin_val = origin_match.group(1)
                origin_val = re.sub(r'^from\s+', '', origin_val)
                updated.append({"entity": "origin", "value": origin_val})
            else:
                disp.utter_message(text="Please specify the source like 'from 10.0.0.1'.")
                return [SlotSet("awaiting_feedback", True), SlotSet("missing_entity_type", missing_type)]
        elif missing_type == "destination":
            dest_match = re.search(r'(?:to\s+)?(\S+)', user_text)
            if dest_match:
                dest_val = dest_match.group(1)
                dest_val = re.sub(r'^to\s+', '', dest_val)
                updated.append({"entity": "destination", "value": dest_val})
            else:
                disp.utter_message(text="Please specify the destination like 'to cam'.")
                return [SlotSet("awaiting_feedback", True), SlotSet("missing_entity_type", missing_type)]
        elif missing_type == "origin_destination":
            from_match = re.search(r'from\s+(\S+)', user_text)
            to_match = re.search(r'to\s+(\S+)', user_text)
            if from_match:
                updated.append({"entity": "origin", "value": from_match.group(1)})
            if to_match:
                updated.append({"entity": "destination", "value": to_match.group(1)})
            if not from_match and not to_match:
                disp.utter_message(text="Please specify the source and destination like 'from temp1 to temp2'.")
                return [SlotSet("awaiting_feedback", True), SlotSet("missing_entity_type", missing_type)]
        elif missing_type == "energy_config":
            of_match = re.search(r'\b(OF0|MRHOF|ADAPTIVE-OF)\b', user_text, re.IGNORECASE)
            if of_match:
                updated.append({"entity": "objective_function", "value": of_match.group(1).upper()})
            else:
                updated.append({"entity": "objective_function", "value": user_text})
        elif missing_type == "sensor":
            sensor_match = re.search(r'\b(temperature|humidity|motion|light|pressure|co2|pm2\.5|voc|no2|ozone|air quality)\b', user_text, re.IGNORECASE)
            if sensor_match:
                updated.append({"entity": "sensor_type", "value": sensor_match.group(1).lower()})
            else:
                updated.append({"entity": "sensor_type", "value": user_text})
        elif missing_type == "location":
            updated.append({"entity": "location", "value": user_text})
        elif missing_type == "query_type":
            updated.append({"entity": "query_type", "value": user_text.replace(' ', '_')})
        elif missing_type == "simulation_duration":
            updated.append({"entity": "simulation_duration", "value": user_text})
        else:
            manual_entities = _extract_entities_manually(user_text)
            for ent in manual_entities:
                if not any(e.get("entity") == ent["entity"] and e.get("value") == ent["value"] for e in updated):
                    updated.append(ent)

        missing = _check_required_entities(updated, user_text)
        if not missing:
            parsed = parse_entities(updated)
            try:
                nile = _build_nile_fallback(updated, user_text)
            except Exception as e:
                nile = "allow traffic"
            nice_text = _format_nice_intent(nile)
            disp.utter_message(text=nice_text)
            disp.utter_message(text="Is this correct now?")
            return [
                SlotSet("pending_nile_command", nile),
                SlotSet("pending_confirmation", True),
                SlotSet("parsed_entities", parsed),
                SlotSet("original_entities", updated),
                SlotSet("awaiting_feedback", False),
                SlotSet("entity", None),
                SlotSet("value", None),
                SlotSet("missing_entity_type", None),
            ]

        make_simple_response(disp, "I still need more information.")
        make_simple_response(disp, missing["msg"])
        return [
            SlotSet("original_entities", updated),
            SlotSet("missing_entity_type", missing.get("missing")),
            SlotSet("awaiting_feedback", True),
            SlotSet("entity", None),
            SlotSet("value", None),
        ]

class ActionDeploy(Action):
    def name(self): return "action_deploy"
    def run(self, disp, tracker, domain):
        # Deploy pending Nile command.
        pending = tracker.get_slot("pending_nile_command")
        if not pending and db:
            intent = db.get_latest_intent(tracker.sender_id)
            pending = intent.get("nile") if intent else None
        if pending:
            if db:
                intent = db.get_latest_intent(tracker.sender_id)
                if intent and intent.get("_id"):
                    db.update_intent(intent["_id"], {"status": "confirmed"})
            _deploy_nile(pending, disp)
        else:
            make_simple_response(disp, "No pending policy to deploy.")
        return [
            SlotSet("pending_confirmation", False),
            SlotSet("pending_nile_command", None),
            SlotSet("parsed_entities", None),
        ]

class ActionFeedback(Action):
    def name(self): return "action_feedback"
    def run(self, disp, tracker, domain):
        # Reuse feedback handler.
        return ActionBuild()._handle_as_feedback(disp, tracker)

class ActionFeedbackConfirm(Action):
    def name(self): return "action_feedback_confirm"
    def run(self, disp, tracker, domain):
        # Confirm and deploy if no feedback is pending.
        if tracker.get_slot("awaiting_feedback"):
            disp.utter_message(text="Please complete the feedback first or cancel.")
            return []
        pending = tracker.get_slot("pending_nile_command")
        if pending:
            _deploy_nile(pending, disp)
        else:
            disp.utter_message(text="No pending policy to deploy.")
        return [
            SlotSet("pending_confirmation", False),
            SlotSet("pending_nile_command", None),
            SlotSet("parsed_entities", None),
        ]

class ActionRoboticsLaws(Action):
    def name(self): return "action_robotics_laws"
    def run(self, disp, tracker, domain):
        # Return robotics laws.
        make_simple_response(disp,
            "The three laws of robotics are:\n"
            "1) A robot may not injure a human being or, through inaction, allow a human being to come to harm.\n"
            "2) A robot must obey the orders given it by human beings, except where such orders would conflict with the First Law.\n"
            "3) A robot must protect its own existence as long as such protection does not conflict with the First or Second Law."
        )
        return []

class ActionCancel(Action):
    def name(self) -> Text: return "action_cancel"
    def run(self, dispatcher, tracker, domain):
        # Reset conversation state.
        dispatcher.utter_message(text="Okay. Please start over then.")
        return [
            SlotSet("entity", None), SlotSet("value", None),
            SlotSet("missing_entity_type", None), SlotSet("missing_entity_value", None),
            SlotSet("awaiting_feedback", False),
            SlotSet("pending_confirmation", False), SlotSet("pending_nile_command", None),
            SlotSet("parsed_entities", None), SlotSet("original_entities", None),
            SlotSet("pending_message", None),
        ]
