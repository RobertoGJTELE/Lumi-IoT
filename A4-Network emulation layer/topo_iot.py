#Mininet script that defines the topology with 6 hosts (cameras and sensors).
#!/usr/bin/python3
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.cli import CLI
from mininet.link import TCLink

def my_topo():

    net = Mininet(controller=RemoteController, link=TCLink)

    c0 = net.addController('c0', ip='127.0.0.1', port=6653)

    s1 = net.addSwitch('s1', protocols='OpenFlow13')

    # CLASSROOM 1
    cam = net.addHost('cam', ip='10.0.0.1/24')
    air = net.addHost('air', ip='10.0.0.2/24')
    tmp = net.addHost('tmp', ip='10.0.0.3/24')
    # CLASSROOM 2
    cam2 = net.addHost('cam2', ip='10.0.0.4/24')
    air2 = net.addHost('air2', ip='10.0.0.5/24')
    tmp2 = net.addHost('tmp2', ip='10.0.0.6/24')

    net.addLink(cam, s1, bw=15, delay='30ms')
    net.addLink(air, s1, bw=0.8, delay='20ms')
    net.addLink(tmp, s1, bw=0.3, delay='20ms')

    net.addLink(cam2, s1, bw=15, delay='30ms')
    net.addLink(air2, s1, bw=0.8, delay='20ms')
    net.addLink(tmp2, s1, bw=0.3, delay='20ms')

    net.start()
    CLI(net)
    net.stop()

if __name__ == '__main__':
    my_topo()

