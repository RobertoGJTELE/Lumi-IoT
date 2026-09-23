#Core of the compiler that invokes the Nile parser, topology modules, and ONOS modules to deploy flow rules.
# compiler.py - Compile an intent Nile into ONOS streams and manage queues/bandwidth.
import re
import itertools
from .nile_parser import parse
from .onos_deployer import (
    get_device_id_and_port_by_ip,
    install_flow,
    delete_all_deployer_flows,
    IP_TO_QUEUE_UDP,
    IP_TO_QUEUE_TCP,
    update_queue_rate,
)
from . import topology

FALLBACK_IP_MAP = {
    'temp1': '10.0.0.1', 'hum1': '10.0.0.2', 'air1': '10.0.0.3',
    'temp2': '10.0.0.4', 'hum2': '10.0.0.5', 'air2': '10.0.0.6',
}

def clean_value(s):
    return s.strip("()'\" ") if isinstance(s, str) else s

def extract_bandwidth(ops, text):
    for op in ops:
        if op.get('type') == 'set' and op.get('function') == 'bandwidth':
            val = clean_value(op.get('value', ''))
            match = re.search(r"(\d+(?:\.\d+)?)\s*([KMGT])?", val, re.IGNORECASE)
            if match:
                num, unit = float(match.group(1)), (match.group(2) or 'M').upper()
                mult = {'K':1, 'M':1000, 'G':1000000, 'T':1000000000}
                return int(num * mult.get(unit, 1000))
    for pat in [
        r'bandwidth\s*\(\s*[\'"]?(\d+(?:\.\d+)?)\s*([KMGT])?[\'"]?\s*\)',
        r'bandwidth\s*[=:]?\s*(\d+(?:\.\d+)?)\s*([KMGT])?'
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            num, unit = float(m.group(1)), (m.group(2) or 'M').upper()
            mult = {'K':1, 'M':1000, 'G':1000000, 'T':1000000000}
            return int(num * mult.get(unit, 1000))
    return None

def resolve_target(spec):
    func, val = spec.get('function'), clean_value(spec.get('value'))
    if re.match(r'^\d+\.\d+\.\d+\.\d+$', val):
        return [val]
    if func in ('endpoint', 'group', 'location', 'service', 'traffic'):
        ips = topology.get_ip_by_handle(val)
        if ips:
            return ips
        fallback = FALLBACK_IP_MAP.get(val.lower())
        if fallback:
            return [fallback]
        raise ValueError(f"Unknown {func}: {val}")
    raise ValueError(f"Unsupported target function: {func}")

def handle_request(request):
    intent = request.get('intent')
    if not intent:
        return {'status': {'code': 400, 'details': 'Missing intent'}}

    try:
        parsed = parse(intent)
        print(f"[COMPILER] Parsed: {parsed}")
    except Exception as e:
        return {'status': {'code': 400, 'details': f'Parse error: {e}'}}

    intent_lower = intent.lower()
    proto = None
    proto_name = "IP"
    if 'udp' in intent_lower:
        proto = 17
        proto_name = "UDP"
    elif 'tcp' in intent_lower:
        proto = 6
        proto_name = "TCP"
    elif 'icmp' in intent_lower:
        proto = 1
        proto_name = "ICMP"
    print(f"[COMPILER] Protocolo detectado: {proto_name}")

    ops = parsed.get('operations', [])
    if ops:
        op_type = ops[0].get('type', 'allow')
    else:
        lower = intent.lower()
        if 'block' in lower or 'deny' in lower:
            op_type = 'block'
        elif 'limit' in lower or 'set bandwidth' in lower:
            op_type = 'set'
        else:
            op_type = 'allow'

    bw_kbps = None
    if op_type == 'set':
        bw_kbps = extract_bandwidth(ops, intent)
        if bw_kbps is None:
            return {'status': {'code': 400, 'details': 'Could not extract bandwidth'}}
        print(f"[COMPILER] bandwidth_kbps={bw_kbps}")

    try:
        src = resolve_target(parsed['origin']) if 'origin' in parsed else topology.get_all_ips()
        dst = []
        if 'destination' in parsed:
            dst = resolve_target(parsed['destination'])
        elif 'targets' in parsed:
            for t in parsed['targets']:
                dst.extend(resolve_target(t))
        else:
            dst = [ip for ip in topology.get_all_ips() if ip not in src]
        print(f"[COMPILER] src={src}, dst={dst}")
    except Exception as e:
        return {'status': {'code': 400, 'details': f'Target error: {e}'}}

    if not src or not dst:
        return {'status': {'code': 400, 'details': 'No valid IPs'}}

    device_id, _ = get_device_id_and_port_by_ip(src[0])
    print(f"[COMPILER] device_id={device_id}")

    msgs = []
    for s, d in itertools.product(src, dst):
        if s == d:
            continue
        try:
            print(f"[COMPILER] Procesando {s} -> {d} (proto={proto_name})")
            delete_all_deployer_flows(device_id, s, d)

            if op_type == 'block':
                install_flow(device_id, s, action='deny', direction='src', proto=proto)
                install_flow(device_id, d, action='deny', direction='dst', proto=proto)
                msgs.append(f"Blocked {proto_name} {s} ↔ {d}")

            elif op_type == 'set' and bw_kbps:
                rate_bps = bw_kbps * 1000
                print(f"[COMPILER] ===== UNA SOLA COLA (ORIGEN) =====")
                print(f"[COMPILER] Actualizando cola del ORIGEN ({s})")
                update_queue_rate(s, rate_bps, proto=proto)

                # Obtener queue ID para el origen
                if proto == 17:
                    qid_src = IP_TO_QUEUE_UDP.get(s)
                elif proto == 6:
                    qid_src = IP_TO_QUEUE_TCP.get(s)
                else:
                    qid_src = None

                if qid_src is None:
                    raise Exception(f"IP {s} sin queue_id para {proto_name}")

                print(f"[COMPILER] Instalando flujo {s}->{d} con queue={qid_src}")
                install_flow(device_id, s, queue_id=qid_src, direction='src', proto=proto)
                print(f"[COMPILER] Instalando flujo {d}->{s} con queue={qid_src}")
                install_flow(device_id, d, queue_id=qid_src, direction='src', proto=proto)

                msgs.append(f"Limited {proto_name} {s} ↔ {d} to {bw_kbps} kbps (queue {qid_src})")

            else:
                msgs.append(f"Allowed {proto_name} {s} ↔ {d}")

        except Exception as e:
            print(f"[COMPILER] Error: {e}")
            msgs.append(f"Error {s}->{d}: {e}")

    return {
        'status': {'code': 200, 'details': '; '.join(msgs)},
        'input': {'type': 'nile', 'intent': intent},
        'output': {'operation': op_type, 'src_ips': src, 'dst_ips': dst, 'bandwidth_kbps': bw_kbps}
    }
