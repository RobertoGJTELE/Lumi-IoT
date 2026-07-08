#Declaration of the custom Rasa actions for building, deploying, managing feedback, confirming deployment, canceling sessions, and responding to robotics law queries.
import json
import os
import re
import traceback
import requests
from typing import Any, Text, Dict, List, Optional
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet

try:
    from nile import builder as nile_builder
    from .parser import parse_entities, parse_feedback
except ImportError:
    nile_builder = None
    parse_entities = None
    parse_feedback = None

from database.client import Database
from .beautifier import beautify_intent_colored

DEPLOY_URL = "http://192.168.56.10:5000/deploy"

try:
    db = Database()
    print("[Webhook] MongoDB connected.")
except Exception as e:
    print(f"[Webhook] MongoDB error: {e}")
    db = None

def make_simple_response(dispatcher, text, suggestions=None):
    buttons = [{"title": s, "payload": s} for s in suggestions] if suggestions else None
    dispatcher.utter_message(text=text, buttons=buttons)

def make_card_response(dispatcher, title, subtitle, text, formatted_text, suggestions=None):
    card = f"**{title}**\n*{subtitle}*\n\n{formatted_text}\n\n{text}"
    buttons = [{"title": s, "payload": s} for s in suggestions] if suggestions else None
    dispatcher.utter_message(text=card, buttons=buttons)

def _format_nice_intent(nile: str) -> str:
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

def _build_nile(entities):
    origin = None
    destination = None
    operation = None
    bandwidth_value = None
    for e in entities:
        e_type = e.get("entity")
        e_value = e.get("value")
        if e_type in ("origin", "endpoint"):
            if not origin:
                origin = e_value
        elif e_type in ("destination", "endpoint"):
            if not destination:
                destination = e_value
        elif e_type == "operation":
            operation = e_value.lower()
        elif e_type == "qos_value":
            bandwidth_value = e_value
        elif e_type == "qos_constraint":
            pass
    parts = []
    if origin:
        parts.append(f"from endpoint('{origin}')")
    if destination:
        parts.append(f"to endpoint('{destination}')")
    if operation and operation in ("block", "deny", "prevent"):
        action = "action deny"
    else:
        action = "action allow"
    if bandwidth_value:
        parts.append(f"bandwidth {bandwidth_value}")
    core = " ".join(parts) + " " + action
    nile = f"define intent lumi_intentIntent: {core}"
    return nile

def _deploy_nile(nile_cmd, disp):
    try:
        r = requests.post(DEPLOY_URL, json={"intent": nile_cmd}, timeout=10)
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

ENTITY_TYPES = [
    "location", "group", "middlebox", "service", "traffic",
    "protocol", "device", "sensor_type", "air_quality_parameter", "air_quality_sensor"
]

class ActionBuild(Action):
    def name(self): return "action_build"
    def run(self, disp, tracker, domain):
        if tracker.get_slot("awaiting_feedback"):
            return self._handle_as_feedback(disp, tracker)
        try:
            entities = tracker.latest_message.get("entities", [])
            text = tracker.latest_message.get("text", "")
            nile = _build_nile(entities)
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
            ]
        if last_intent == "reject":
            entity = None
            value = None
        else:
            entity = tracker.get_slot("entity")
            value = tracker.get_slot("value")
            if entity and entity not in ENTITY_TYPES:
                entity = None
        if not entity:
            disp.utter_message(text="Hmm, okay. What did I miss?")
            disp.utter_message(response="utter_ask_entity")
            return [
                SlotSet("awaiting_feedback", True),
                SlotSet("entity", None),
                SlotSet("value", None),
            ]
        if not value:
            user_text = tracker.latest_message.get("text", "").strip()
            if user_text.lower() in ENTITY_TYPES:
                new_entity = user_text.lower()
                disp.utter_message(response="utter_ask_value", entity=new_entity)
                return [
                    SlotSet("entity", new_entity),
                    SlotSet("awaiting_feedback", True),
                ]
            else:
                value = user_text
                disp.utter_message(text=f"Understood: '{value}' is a {entity}.")
        if entity and value:
            if db:
                intent = db.get_latest_intent(tracker.sender_id)
                if intent and intent.get("_id"):
                    missing = intent.get("missingEntities", {})
                    if entity not in missing:
                        missing[entity] = {}
                    missing[entity][value] = True
                    db.update_intent(intent["_id"], {"missingEntities": missing})
            orig_entities = list(tracker.get_slot("original_entities") or [])
            orig_entities.append({"entity": entity, "value": value})
            try:
                nile = _build_nile(orig_entities)
                nice_text = _format_nice_intent(nile)
                disp.utter_message(text=nice_text)
                disp.utter_message(text="Is this correct now?")
                return [
                    SlotSet("pending_nile_command", nile),
                    SlotSet("pending_confirmation", True),
                    SlotSet("missing_entity_type", None),
                    SlotSet("missing_entity_value", None),
                    SlotSet("awaiting_feedback", False),
                    SlotSet("value", value),
                ]
            except Exception as e:
                disp.utter_message(text=f"Error rebuilding: {e}")
                return []
        else:
            return [SlotSet("awaiting_feedback", True)]

class ActionDeploy(Action):
    def name(self): return "action_deploy"
    def run(self, disp, tracker, domain):
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
        ]

class ActionFeedback(Action):
    def name(self): return "action_feedback"
    def run(self, disp, tracker, domain):
        return ActionBuild()._handle_as_feedback(disp, tracker)

class ActionFeedbackConfirm(Action):
    def name(self): return "action_feedback_confirm"
    def run(self, disp, tracker, domain):
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
        ]

class ActionRoboticsLaws(Action):
    def name(self): return "action_robotics_laws"
    def run(self, disp, tracker, domain):
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
        dispatcher.utter_message(text="Okay. Please start over then.")
        return [
            SlotSet("entity", None), SlotSet("value", None),
            SlotSet("missing_entity_type", None), SlotSet("missing_entity_value", None),
            SlotSet("awaiting_feedback", False),
            SlotSet("pending_confirmation", False), SlotSet("pending_nile_command", None),
        ]
