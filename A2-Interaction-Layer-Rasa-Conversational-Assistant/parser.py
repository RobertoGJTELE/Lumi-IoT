
#This module contains functions to parse and normalize entities/parameters from Dialogflow or Rasa into a unified dictionary format
from typing import List, Dict, Any, Union

def to_camel_case(string: str) -> str:
    output = ''.join(x for x in string.title() if x.isalnum())
    return output[0].lower() + output[1:]

def extract_from_dialogflow(request: Dict[str, Any]) -> Dict[str, List[Any]]:
    result = request.get("queryResult", {})
    parameters = result.get("parameters", {})
    temp: Dict[str, List[Any]] = {}
    for key, value in parameters.items():
        if not value:
            continue
        if isinstance(value, list):
            temp[key] = value
        else:
            temp[key] = [value]
    return temp

def extract_from_rasa(rasa_entities: List[Dict[str, Any]]) -> Dict[str, List[Any]]:
    temp: Dict[str, List[Any]] = {}
    for ent in rasa_entities:
        etype = ent.get("entity")
        value = ent.get("value")
        if etype not in temp:
            temp[etype] = []
        temp[etype].append(value)
    return temp

def normalize_value(val: Any) -> Any:
    if isinstance(val, dict):
        return next(iter(val.values()), "")
    return val

def parse_entities(data: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Dict[str, Any]:
    if isinstance(data, dict) and "queryResult" in data:
        temp = extract_from_dialogflow(data)
        intent_id = data["queryResult"]["intent"]["displayName"]
    else:
        temp = extract_from_rasa(data)
        intent_id = "lumi_intent"

    entities: Dict[str, Any] = {
        "id": intent_id,
        "targets": [],
        "operations": [],
        "middleboxes": [],
        "services": [],
        "traffics": [],
        "protocols": [],
        "locations": [],
        "sensor_types": [],
        "air_quality_params": [],
        "devices": [],
        "air_quality_sensors": [],
    }

    for field in ["origin", "destination", "start", "end"]:
        if field in temp:
            entities[field] = normalize_value(temp[field][0])

    direct_mapping = {
        "operation": "operations",
        "middlebox": "middleboxes",
        "service": "services",
        "traffic": "traffics",
        "protocol": "protocols",
        "location": "locations",
        "sensor_type": "sensor_types",
        "air_quality_parameter": "air_quality_params",
        "device": "devices",
        "air_quality_sensor": "air_quality_sensors",
    }
    for src, dst in direct_mapping.items():
        if src in temp:
            entities[dst] = [normalize_value(x) for x in temp[src]]

    if entities["locations"]:
        for loc in entities["locations"]:
            entities["targets"].append({"location": loc})

    if "target" in temp:
        for group in temp["target"]:
            if isinstance(group, list):
                for inner in group:
                    if isinstance(inner, dict) and "entity" in inner and "value" in inner:
                        entities["targets"].append({inner["entity"]: inner["value"]})
            elif isinstance(group, dict):
                entities["targets"].append(group)

    if "qos_value" in temp:
        metrics = temp.get("qos_metric", [])
        values = temp.get("qos_value", [])
        units = temp.get("qos_unit", [])
        constraints = temp.get("qos_constraint", [])
        if not metrics and units:
            metrics = ["bandwidth" if "ps" in str(u) else "quota" for u in units]
        qos_list = []
        for i in range(len(values)):
            value = normalize_value(values[i])
            qos = {"value": value}
            if i < len(metrics):
                qos["name"] = to_camel_case(metrics[i])
            else:
                qos["name"] = "bandwidth" if (i < len(units) and "ps" in str(units[i])) else "quota"
            if i < len(units):
                qos["unit"] = units[i]
            if i < len(constraints):
                c = constraints[i]
                qos["constraint"] = c if c in ["max","min"] else ("max" if qos["name"]=="bandwidth" else "download")
            else:
                qos["constraint"] = "max" if qos["name"]=="bandwidth" else "download"
            qos_list.append(qos)
        entities["qos"] = qos_list

    return entities

def parse_feedback(data: Union[Dict[str, Any], Any]) -> Dict[str, str]:
    feedback = {}
    if isinstance(data, dict) and "queryResult" in data:
        params = data["queryResult"].get("parameters", {})
        if "entity" in params:
            feedback["entity"] = params["entity"]
        if "value" in params:
            feedback["value"] = params["value"]
    elif hasattr(data, "latest_message"):
        entities = data.latest_message.get("entities", [])
        for ent in entities:
            if ent["entity"] == "entity":
                feedback["entity"] = ent["value"]
            elif ent["entity"] == "value":
                feedback["value"] = ent["value"]
    return feedback
