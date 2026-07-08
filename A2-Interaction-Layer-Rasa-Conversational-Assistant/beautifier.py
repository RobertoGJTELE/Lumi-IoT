#Declaration of the HTML formatting utility that applies colored spans to Nile intent components (operations, target keys, thresholds, time ranges, and quoted strings) for improved readability in the UI.

# beautifier.py -Coloring of Nile intents in HTML format
import re

COLOR_CONFIG = {
    "operations": {
        "words": [
            'add', 'remove', 'allow', 'block', 'set', 'unset', 'monitor',
            'from', 'to', 'start', 'end', 'for'
        ],
        "color": "#007bff",
    },
    "target_keys": {
        "words": [
            'endpoint', 'sensor_type', 'air_quality_parameter',
            'air_quality_sensor', 'device', 'service', 'traffic',
            'protocol', 'group', 'middlebox', 'location'
        ],
        "color": "#28a745",
    },
    "threshold": {
        "pattern": r"threshold\('[^']+',\s*'[^']+'\)",
        "color": "#fd7e14",
    },
    "time": {
        "pattern": r"start hour\('[^']+'\) end hour\('[^']+'\)",
        "color": "#6f42c1",
    },
    "quoted_string": {
        "pattern": r"'[^']*'",
        "color": "#6c757d",
    },
}

def beautify_intent_colored(nile: str) -> str:
    tokens = []
    i = 0
    while i < len(nile):
        if nile[i] == "'":
            try:
                end = nile.index("'", i + 1)
            except ValueError:
                end = len(nile) - 1
            tokens.append(("quoted", nile[i:end + 1]))
            i = end + 1
        else:
            next_quote = nile.find("'", i)
            if next_quote == -1:
                tokens.append(("text", nile[i:]))
                break
            tokens.append(("text", nile[i:next_quote]))
            i = next_quote

    result = ""
    for token_type, token_text in tokens:
        if token_type == "quoted":
            result += f"<span style='color:{COLOR_CONFIG['quoted_string']['color']}'>{token_text}</span>"
        else:
            mod_text = token_text
            mod_text = re.sub(
                COLOR_CONFIG['threshold']['pattern'],
                lambda m: f"<span style='color:{COLOR_CONFIG['threshold']['color']}'>{m.group(0)}</span>",
                mod_text
            )
            mod_text = re.sub(
                COLOR_CONFIG['time']['pattern'],
                lambda m: f"<span style='color:{COLOR_CONFIG['time']['color']}'>{m.group(0)}</span>",
                mod_text
            )
            for category in ("operations", "target_keys"):
                for word in COLOR_CONFIG[category]["words"]:
                    pattern = r'\b' + re.escape(word) + r'\b'
                    mod_text = re.sub(
                        pattern,
                        lambda m, c=COLOR_CONFIG[category]["color"]:
                            f"<span style='color:{c}'>{m.group(0)}</span>",
                        mod_text
                    )
            result += mod_text
    return result
