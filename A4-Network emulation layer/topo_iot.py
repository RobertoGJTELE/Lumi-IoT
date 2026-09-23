#Mininet script that defines the topology with 6 hosts (cameras and sensors).
#!/usr/bin/env python3
import json
import requests
from time import sleep
from mininet.log import setLogLevel, info
from mininet.node import RemoteController
from mn_wifi.cli import CLI
from mn_wifi.net import Mininet_wifi

ONOS_URL = "http://127.0.0.1:8181/onos/v1"
ONOS_USER = "onos"
ONOS_PASS = "rocks"
DEVICE_ID = "of:1000000000000001"

def get_wifi_iface(sta):
    if hasattr(sta, 'wintf'):
        return sta.wintf.name
    if hasattr(sta, 'defaultIntf'):
        return sta.defaultIntf().name
    ifaces = sta.cmd('ip -br link show').strip().split('\n')
    for line in ifaces:
        parts = line.split()
        if len(parts) >= 1:
            ifname = parts[0].replace('@', '')
            if ifname.startswith(('wlan', 'wlp', 'wl', 'ath')):
                return ifname
    return 'wlan0'

def install_flow(device_id, eth_type, ip_proto=None, in_port=1, out_ports=["IN_PORT"], priority=60000):
    criteria = [{"type": "IN_PORT", "port": str(in_port)}]
    if eth_type:
        criteria.append({"type": "ETH_TYPE", "ethType": eth_type})
    if ip_proto is not None:
        criteria.append({"type": "IP_PROTO", "protocol": ip_proto})
    instructions = [{"type": "OUTPUT", "port": str(port)} for port in out_ports]
    flow = {
        "priority": priority,
        "timeout": 0,
        "isPermanent": True,
        "deviceId": device_id,
        "selector": {"criteria": criteria},
        "treatment": {"instructions": instructions}
    }
    resp = requests.post(
        f"{ONOS_URL}/flows/{device_id}",
        auth=requests.auth.HTTPBasicAuth(ONOS_USER, ONOS_PASS),
        data=json.dumps(flow),
        headers={"Content-Type": "application/json"},
        timeout=5
    )
    if resp.status_code >= 400:
        raise Exception(f"ONOS error {resp.status_code}: {resp.text[:200]}")

def topology():
    net = Mininet_wifi()

    ap1 = net.addAccessPoint(
        'ap1',
        ssid="test-onos",
        mode="g",
        channel="5",
        protocols='OpenFlow13',
        client_isolation=True,
        failMode='secure'
    )

    temp1 = net.addStation('temp1', ip='10.0.0.1/24')
    hum1  = net.addStation('hum1',  ip='10.0.0.2/24')
    air1  = net.addStation('air1',  ip='10.0.0.3/24')

    temp2 = net.addStation('temp2', ip='10.0.0.4/24')
    hum2  = net.addStation('hum2',  ip='10.0.0.5/24')
    air2  = net.addStation('air2',  ip='10.0.0.6/24')

    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.2',
        port=6653,
        protocols='OpenFlow13'
    )

    net.configureNodes()

    net.addLink(temp1, ap1)
    net.addLink(hum1,  ap1)
    net.addLink(air1,  ap1)
    net.addLink(temp2, ap1)
    net.addLink(hum2,  ap1)
    net.addLink(air2,  ap1)

    net.build()
    c0.start()
    ap1.start([c0])
    sleep(3)

    iface = get_wifi_iface(temp1)
    temp1.cmd('tc qdisc add dev %s root netem delay 30ms 5ms loss 0.2%%' % iface)
    iface = get_wifi_iface(temp2)
    temp2.cmd('tc qdisc add dev %s root netem delay 30ms 5ms loss 0.2%%' % iface)

    iface = get_wifi_iface(hum1)
    hum1.cmd('tc qdisc add dev %s root netem delay 20ms 2ms loss 0.2%%' % iface)
    iface = get_wifi_iface(air1)
    air1.cmd('tc qdisc add dev %s root netem delay 20ms 2ms loss 0.2%%' % iface)
    iface = get_wifi_iface(hum2)
    hum2.cmd('tc qdisc add dev %s root netem delay 20ms 2ms loss 0.2%%' % iface)
    iface = get_wifi_iface(air2)
    air2.cmd('tc qdisc add dev %s root netem delay 20ms 2ms loss 0.2%%' % iface)

    ap1.cmd('ovs-vsctl clear port ap1-wlan1 qos')
    ap1.cmd('ovs-vsctl --all destroy QoS')
    ap1.cmd('ovs-vsctl --all destroy Queue')

    queue_uuids = []
    for i in range(12):
        rate = 1000000000
        burst = 100000000
        cmd = f'ovs-vsctl create queue other-config:max-rate={rate} other-config:burst={burst}'
        result = ap1.cmd(cmd)
        queue_uuid = result.strip()
        if 'error' in result.lower() or len(queue_uuid) < 10:
            pass
        else:
            queue_uuids.append(queue_uuid)

    if len(queue_uuids) == 12:
        queues_str = ' '.join([f'queues:{i}={queue_uuids[i]}' for i in range(12)])
        cmd_qos = f'ovs-vsctl -- set port ap1-wlan1 qos=@q -- --id=@q create qos type=linux-htb {queues_str}'
        result_qos = ap1.cmd(cmd_qos)
        if 'error' in result_qos.lower():
            pass
        else:
            pass
    else:
        pass

    ap1.cmd('ovs-ofctl -O OpenFlow13 del-flows ap1')

    install_flow(DEVICE_ID, "0x0806", out_ports=["CONTROLLER", "IN_PORT"], priority=60000)
    install_flow(DEVICE_ID, "0x0800", ip_proto=1, out_ports=["IN_PORT"], priority=60000)
    install_flow(DEVICE_ID, "0x0800", ip_proto=None, out_ports=["IN_PORT"], priority=1000)

    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    topology()

