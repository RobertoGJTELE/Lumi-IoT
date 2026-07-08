#Declaration of the MongoDB database connection and CRUD operations for managing expected rules, audit logging, and telemetry history.
# db.py -Connecting to MongoDB and performing CRUD operations for expected rules, auditing, and telemetry.
import pymongo, os, time

class AssuranceDB:
    def __init__(self, uri=None):
        self.uri = uri or os.getenv("MONGODB_URI", "mongodb://172.29.36.54:27017/lumi")
        self.client = pymongo.MongoClient(self.uri)
        self.db = self.client.get_database()

    @property
    def expected_rules(self):
        return self.db.expected_rules

    @property
    def audit_log(self):
        return self.db.assurance_audit

    @property
    def telemetry_history(self):
        return self.db.telemetry_history

    def save_expected_rule(self, rule):
        existing = self.expected_rules.find_one({
            "src_ip": rule["src_ip"],
            "dst_ip": rule["dst_ip"],
            "action": rule["action"],
            "queue_id": rule.get("queue_id")
        })
        if existing:
            print(f"[DB] Rule already exists for {rule['src_ip']}->{rule['dst_ip']} ({rule['action']}). Not duplicated.")
            return existing["_id"]
        rule["created_at"] = time.time()
        return self.expected_rules.insert_one(rule).inserted_id

    def get_expected_rules(self):
        return list(self.expected_rules.find({}))

    def delete_expected_rule_by_match(self, src_ip, dst_ip, action):
        result = self.expected_rules.delete_many({
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "action": action
        })
        if result.deleted_count > 0:
            print(f"[DB] Deleted {result.deleted_count} rule(s) for {src_ip}->{dst_ip} ({action})")
        return result.deleted_count

    def log_audit(self, action, details):
        self.audit_log.insert_one({
            "action": action,
            "details": details,
            "timestamp": time.time()
        })

    def save_telemetry(self, ip, device_id, port, stats):
        self.telemetry_history.insert_one({
            "ip": ip,
            "device_id": device_id,
            "port": port,
            "rx_bytes": stats.get("bytesReceived", 0),
            "tx_bytes": stats.get("bytesSent", 0),
            "packets_received": stats.get("packetsReceived", 0),
            "packets_sent": stats.get("packetsSent", 0),
            "packets_rx_dropped": stats.get("packetsRxDropped", 0),
            "packets_tx_dropped": stats.get("packetsTxDropped", 0),
            "timestamp": time.time()
        })
