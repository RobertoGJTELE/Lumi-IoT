#Core of the compiler that invokes the Nile parser, topology modules, and ONOS modules to deploy flow rules.
# compiler.py - Compile an intent Nile into ONOS streams and manage queues/bandwidth.
import re, hashlib, itertools
from .nile_parser import parse
from .onos_deployer import (
    get_device_id_and_port_by_ip,
    delete_previous_flow,
    create_or_update_queue,
    install_flow
)
from . import topology
from .db import AssuranceDB

db_assurance = AssuranceDB()

def clean_value(value_str):
    return value_str.strip("()'\" ") if isinstance(value_str, str) else value_str

def extract_bandwidth(text):
    match = re.search(r'bandwidth\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(K|M|G|T)?', text, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        unit = (match.group(2) or 'M').upper()
        multipliers = {'K': 1, 'M': 1000, 'G': 1000000, 'T': 1000000000}
        return int(val * multipliers.get(unit, 1000))
    match = re.search(r'bandwidth\s*\(\s*[\'"]?(\d+(?:\.\d+)?)\s*([KMGT])?[\'"]?\s*\)', text, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        unit = (match.group(2) or 'M').upper()
        multipliers = {'K': 1, 'M': 1000, 'G': 1000000, 'T': 1000000000}
        return int(val * multipliers.get(unit, 1000))
    return None

def resolve_target(target_spec):
    func = target_spec.get('function')
    val = clean_value(target_spec.get('value'))
    if func == 'endpoint':
        if re.match(r'\d+\.\d+\.\d+\.\d+', val):
            return [val]
        else:
            ips = topology.get_ip_by_handle(val)
            if ips:
                return ips
            raise ValueError(f"Unknown endpoint: {val}")
    elif func in ('group', 'location', 'service', 'traffic'):
        ips = topology.get_ip_by_handle(val)
        if ips:
            return ips
        raise ValueError(f"Unknown {func}: {val}")
    else:
        raise ValueError(f"Unsupported target function: {func}")

def handle_request(request):
    intent_text = request.get('intent')
    if not intent_text:
        return {'status': {'code': 400, 'details': 'Missing intent'}}

    try:
        parsed = parse(intent_text)
    except Exception as e:
        return {'status': {'code': 400, 'details': f'Parse error: {e}'}}

    operations = parsed.get('operations', [])

    if not operations:
        lower = intent_text.lower()
        if 'block' in lower or 'deny' in lower:
            op_type = 'block'
        elif 'limit' in lower or 'set bandwidth' in lower:
            op_type = 'set'
        else:
            op_type = 'allow'
        operations.append({'type': op_type})
    else:
        op_type = operations[0].get('type', 'allow')

    bandwidth_kbps = None
    if op_type == 'set':
        bandwidth_kbps = extract_bandwidth(intent_text)
        for op in operations:
            if op.get('function') == 'bandwidth':
                val_str = clean_value(op.get('value', ''))
                bw = extract_bandwidth(val_str)
                if bw:
                    bandwidth_kbps = bw
                    break

    src_ips = []
    if 'origin' in parsed:
        src_ips = resolve_target(parsed['origin'])
    dst_ips = []
    if 'destination' in parsed:
        dst_ips = resolve_target(parsed['destination'])
    if not dst_ips and 'targets' in parsed:
        for t in parsed['targets']:
            dst_ips.extend(resolve_target(t))
    if not dst_ips:
        all_ips = topology.get_all_ips()
        dst_ips = [ip for ip in all_ips if ip not in src_ips]
    if not src_ips:
        src_ips = topology.get_all_ips()

    if src_ips:
        device_id, _ = get_device_id_and_port_by_ip(src_ips[0])
    else:
        device_id = "of:0000000000000001"

    status_msgs = []
    for src_ip, dst_ip in itertools.product(src_ips, dst_ips):
        if src_ip == dst_ip:
            continue
        try:
            src_dev, src_port = get_device_id_and_port_by_ip(src_ip)
            dst_dev, dst_port = get_device_id_and_port_by_ip(dst_ip)
            if src_dev != dst_dev:
                print(f" {src_ip} and {dst_ip} are on different switches, skipping")
                continue

            delete_previous_flow(src_dev, src_ip, dst_ip)

            if op_type == 'block':
                install_flow(src_dev, src_ip, dst_ip, action='deny')
                status_msgs.append(f"Blocked {src_ip} → {dst_ip}")
                db_assurance.save_expected_rule({
                    "src_ip": src_ip, "dst_ip": dst_ip,
                    "action": "deny", "output_port": None,
                    "queue_id": None, "device_id": src_dev
                })
            elif op_type == 'allow':
                install_flow(src_dev, src_ip, dst_ip, action='allow', output_port=dst_port)
                status_msgs.append(f"Allowed {src_ip} → {dst_ip}")
                db_assurance.save_expected_rule({
                    "src_ip": src_ip, "dst_ip": dst_ip,
                    "action": "allow", "output_port": dst_port,
                    "queue_id": None, "device_id": src_dev
                })
            elif op_type == 'set' and bandwidth_kbps:
                rate_bps = bandwidth_kbps * 1000
                key = f"{src_ip}:{dst_ip}:{rate_bps}".encode()
                queue_id = (int(hashlib.md5(key).hexdigest(), 16) % 60000) + 1
                create_or_update_queue(src_dev, dst_port, queue_id, rate_bps)
                install_flow(src_dev, src_ip, dst_ip, action='allow',
                             queue_id=queue_id, output_port=dst_port)
                status_msgs.append(f"Limited {src_ip} → {dst_ip} to {bandwidth_kbps} kbps (queue {queue_id})")
                db_assurance.save_expected_rule({
                    "src_ip": src_ip, "dst_ip": dst_ip,
                    "action": "allow_qos", "output_port": dst_port,
                    "queue_id": queue_id, "rate_bps": rate_bps,
                    "device_id": src_dev
                })
            else:
                install_flow(src_dev, src_ip, dst_ip, action='allow', output_port=dst_port)
                status_msgs.append(f"Allowed {src_ip} → {dst_ip}")
                db_assurance.save_expected_rule({
                    "src_ip": src_ip, "dst_ip": dst_ip,
                    "action": "allow", "output_port": dst_port,
                    "queue_id": None, "device_id": src_dev
                })
        except Exception as e:
            status_msgs.append(f"Error {src_ip}→{dst_ip}: {e}")

    if not status_msgs:
        status_msgs.append("No flows installed.")

    return {
        'status': {'code': 200, 'details': '; '.join(status_msgs)},
        'input': {'type': 'nile', 'intent': intent_text},
        'output': {
            'operation': op_type,
            'src_ips': src_ips,
            'dst_ips': dst_ips,
            'bandwidth_kbps': bandwidth_kbps
        }
    }

