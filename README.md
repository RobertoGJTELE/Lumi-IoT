# Lumi-IoT
Lumi-IoT Code Repository

Lumi-IoT is an Internet of Things (IoT) platform that combines Natural Language Processing (NLP) and conversational AI to simplify the management of IoT devices, environmental sensors, and network policies. Powered by Rasa, Lumi-IoT enables users to interact with the system using natural language, making IoT management and network administration more intuitive and accessible.
## Features
- Conversational assistant powered by Rasa.
- Web-based interface developed with HTML, CSS, and JavaScript.
- Natural Language Understanding (NLU) for processing user requests.
- IoT device monitoring and management.
- Integration with the ONOS SDN controller for network policy enforcement.
- Natural language-based network configuration and policy management.
- Modular and extensible architecture designed for research and experimentation.
## Capabilities

Lumi-IoT supports natural language commands to:

- Allow or block communication between IoT devices.
- Configure bandwidth limitations and Quality of Service (QoS) policies.
- Deploy network functions such as Firewall, NAT, and Deep Packet Inspection (DPI).
- Monitor and manage IoT devices.
- Query environmental sensors, including temperature, humidity, air quality, and other IoT measurements.
- Configure and enforce Software-Defined Networking (SDN) policies through the ONOS controller.
- Manage network infrastructure using conversational interactions.

## Example Commands

```text
Block traffic from 10.0.0.1 to 10.0.0.2

Allow traffic from camera to air purifier

Limit bandwidth from camera to gateway to 1 Mbps
```

## Technologies

### Backend
- Python
- Rasa Open Source
- REST API

### SDN and Network Emulation
- ONOS SDN Controller
- Mininet Network Emulator

### Frontend
- HTML5
- CSS3
- JavaScript

### Configuration
- YAML
- JSON
