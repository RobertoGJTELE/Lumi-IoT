#Declaration of the response formatting helpers for generating rich cards and suggestions (Google Assistant payloads), along with Dialogflow context management functions for output context handling.
# response.py - Auxiliary functions for building responses in Actions
from rasa_sdk.executor import CollectingDispatcher
from typing import List, Optional

def make_simple_response(dispatcher: CollectingDispatcher, text: str, suggestions: Optional[List[str]] = None):
    buttons = [{"title": s, "payload": s} for s in suggestions] if suggestions else None
    dispatcher.utter_message(text=text, buttons=buttons)

def make_card_response(
    dispatcher: CollectingDispatcher,
    title: str,
    subtitle: str,
    text: str,
    formatted_text: str,
    suggestions: Optional[List[str]] = None
):
    card = f"**{title}**\n*{subtitle}*\n\n{formatted_text}\n\n{text}"
    buttons = [{"title": s, "payload": s} for s in suggestions] if suggestions else None
    dispatcher.utter_message(text=card, buttons=buttons)

def ask_missing_origin_destination():
    return "What are the source and destination? (e.g., 'from temp1 to temp2')"

def ask_missing_qos_value():
    return "What bandwidth value do you want to set? (e.g., '10M', '5Mbps', '1Gbps')"

def ask_missing_location():
    return "What is the location? (e.g., 'lab', 'server room', 'dorms')"

def ask_missing_sensor_type():
    return "What sensor type do you want to monitor? (e.g., 'temperature', 'humidity', 'motion')"

def ask_missing_air_quality_param():
    return "What air quality parameter do you want to monitor? (e.g., 'CO2', 'PM2.5', 'ozone')"

def ask_missing_operation():
    return "What operation do you want to perform? (block, allow, get, set, monitor, add, remove)"

def ask_missing_entity_generic(entity_type):
    return f"What is the {entity_type}? (e.g., 'server', 'temp1', '10.0.0.1')"
