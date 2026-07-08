#Flask web service exposing the deploy endpoint.
# app.py - Flask server that exposes the /deploy endpoint and initiates security and telemetry.

from __future__ import print_function
import json, os
from flask import Flask, make_response, request
from flask_cors import CORS
from compiler import compiler
from compiler import assurance
from compiler import openconfig_client

app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
def home():
    return "Lumi Deployer APIs"

@app.route("/deploy", methods=["POST"])
def deploy():
    req = request.get_json(silent=True, force=True)
    print("Request:", json.dumps(req, indent=4))
    try:
        res = compiler.handle_request(req)
    except Exception as err:
        print(err)
        res = {"status": {'code': 404, 'details': 'Could not deploy intent.'}}
    res = json.dumps(res, indent=4)
    print("Response:", res)
    r = make_response(res)
    r.headers["Content-Type"] = "application/json"
    return r

if __name__ == "__main__":
    assurance.start_assurance()

    known_ips = ['10.0.0.1', '10.0.0.2', '10.0.0.3', '10.0.0.4', '10.0.0.5', '10.0.0.6']
    for ip in known_ips:
        openconfig_client.monitor_bandwidth(ip, interval=10, save_history=True)
    print("[Deployer] Telemetry started for all known IPs.")

    port = int(os.getenv("PORT", 5000))
    print(f"Starting app on port {port}")
    app.run(debug=False, port=port, host="0.0.0.0")
