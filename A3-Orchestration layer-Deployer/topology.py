#Knowledge base of the IoT topology; contains the HOSTS dictionary and the get_ip_by_handle() function.
# topology.py - Mapping logical entities to IPs of the Mininet topology.
HOSTS = {
    # Classroom 1
    'temp1': {'ip': '10.0.0.1', 'location': 'classroom1', 'type': 'temperature'},
    'hum1':  {'ip': '10.0.0.2', 'location': 'classroom1', 'type': 'humidity'},
    'air1':  {'ip': '10.0.0.3', 'location': 'classroom1', 'type': 'air_quality'},
    # Classroom 2
    'temp2': {'ip': '10.0.0.4', 'location': 'classroom2', 'type': 'temperature'},
    'hum2':  {'ip': '10.0.0.5', 'location': 'classroom2', 'type': 'humidity'},
    'air2':  {'ip': '10.0.0.6', 'location': 'classroom2', 'type': 'air_quality'},
}

def get_ip_by_handle(handle: str):
    handle_lower = handle.lower()
    ips = set()

    if handle_lower in HOSTS:
        return [HOSTS[handle_lower]['ip']]

    for name, info in HOSTS.items():
        if info['location'].lower() == handle_lower:
            ips.add(info['ip'])

    for name, info in HOSTS.items():
        if info['type'].lower() == handle_lower:
            ips.add(info['ip'])

    if handle_lower == 'sensors':
        for name, info in HOSTS.items():
            ips.add(info['ip'])
    elif handle_lower == 'classroom1':
        for name, info in HOSTS.items():
            if info['location'] == 'classroom1':
                ips.add(info['ip'])
    elif handle_lower == 'classroom2':
        for name, info in HOSTS.items():
            if info['location'] == 'classroom2':
                ips.add(info['ip'])

    return list(ips) if ips else None

def get_all_ips():
    return [info['ip'] for info in HOSTS.values()]

IP_TO_HOST = {info['ip']: name for name, info in HOSTS.items()}
