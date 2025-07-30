import os
import json
import base64
import requests
from flask import Flask, request, make_response

app = Flask(__name__)

# === CONFIGURATION ===
SLACK_TOKEN = os.environ.get("SLACK_TOKEN")
TILITER_API_KEY = os.environ.get("TILITER_API_KEY")
TILITER_URL = 'https://api.ai.vision.tiliter.com/api/v1/inference/object-counter'

# In-memory tracking of processed events
processed_events = set()

@app.route("/")
def health():
    return "Slack bot is running.", 200

@app.route("/events", methods=["POST"])
def slack_events():
    data = request.json
    print("📩 Incoming Slack event:")
    print(json.dumps(data, indent=2))

    # Handle Slack URL verification
    if data.get("type") == "url_verification":
        return make_response(data["challenge"], 200, {"Content-Type": "text/plain"})

    # Ignore duplicate events
    event_id = data.get("event_id")
    if event_id in processed_events:
        print("⏩ Duplicate event ignored.")
        return make_response("Duplicate", 200)
    processed_events.add(event_id)

    # Process file messages
    if data.get("type") == "event_callback":
        event = data.get("event", {})
        if event.get("type") == "message" and 'files' in event:
            for file in event['files']:
                if file.get('mimetype', '').startswith('image/'):
                    image_url = file['url_private']
                    channel = event['channel']
                    thread_ts = event['ts']
            
                    user_text = event.get("text", "").strip().lower()
                    object_name = None
            
                    if user_text.startswith("count"):
                        object_name = user_text.replace("count", "").strip()
            
                    result = handle_image(image_url, object_name)
                    post_to_slack(channel, thread_ts, result)

        return make_response("OK", 200)

    return make_response("Ignored", 200)

def format_agent_response(agent_type, response_json):
    try:
        if agent_type == "object-counter":
            result = response_json.get("result", {})
            counts = result.get("object_counts", {})
            total = result.get("total_objects", 0)
            details = "\n".join([f"🔹 `{k}` — `{v}`" for k, v in counts.items()])
            return f"✅ *Object Counter*\n- *Total:* {total}\n\n*Breakdown:*\n{details}"

        elif agent_type == "object-validator":
            results = response_json["validation_results"]
            lines = [f"{r['status']} `{r['object']}` ({r['confidence']*100:.1f}%)" for r in results]
            return f"✅ *Object Validator*\n{chr(10).join(lines)}"

        elif agent_type == "label-validator":
            return f"❌ *Label Validator*\nExpected: `{response_json.get('expected_text')}`\nFound: `{response_json.get('extracted_text')}`\nConfidence: {response_json.get('match_confidence')}"

        elif agent_type == "damage-detector":
            areas = response_json["damage_areas"]
            lines = [
                f"🔸 `{a['type']}` ({a['severity']}) @ {a['location']} – {a['confidence']*100:.1f}%"
                for a in areas
            ]
            return f"🛠️ *Damage Detector*\nLevel: `{response_json['damage_level']}`\n\n" + "\n".join(lines)

        elif agent_type == "cleanliness-score":
            issues = response_json["issues_detected"]
            lines = [f"🔸 `{i['type']}` ({i['severity']}) at `{i['location']}`" for i in issues]
            return f"🧼 *Cleanliness Score: {response_json['score_display']}*\nLevel: {response_json['cleanliness_level']}\n\n" + "\n".join(lines)

        elif agent_type == "text-extractor":
            items = response_json["extracted_text"]
            lines = [f"🔹 `{i['text']}` ({i['confidence']*100:.1f}%)" for i in items]
            return f"📝 *Text Extractor*\nDetected Text Blocks:\n" + "\n".join(lines)

        elif agent_type == "receipt-processor":
            lines = [f"- `{item['name']}` — €{item['price']:.2f}" for item in response_json["items"]]
            return f"🧾 *Receipt: {response_json['merchant']}*\nTotal: €{response_json['total']:.2f}\nDate: {response_json['date']}\n\n*Items:*\n" + "\n".join(lines)

        else:
            return ":grey_question: Unknown agent type. Raw output:\n```" + json.dumps(response_json, indent=2) + "```"

    except Exception as e:
        return f":x: Error formatting response: {str(e)}"

def handle_image(image_url, object_name=None, agent_type="object-counter"):
    print("⬇️ Downloading image from Slack...")
    image_response = requests.get(
        image_url,
        headers={'Authorization': f'Bearer {SLACK_TOKEN}'}
    )

    if image_response.status_code != 200:
        return f":x: Failed to download image. Status: {image_response.status_code}"

    image_b64 = base64.b64encode(image_response.content).decode('utf-8')
    image_data_with_prefix = f"data:image/jpeg;base64,{image_b64}"

    payload = {
        "image_data": image_data_with_prefix
    }

    if agent_type == "object-counter":
        payload["parameter"] = f"count {object_name}" if object_name else "count all"
        if object_name:
            object_list = [o.strip() for o in object_name.split(",") if o.strip()]
            payload["objects_specified"] = object_list
            payload["disable_default_object_detection"] = True
            print(f"🔍 Parsed object list: {object_list}")

    elif agent_type == "label-validator":
        payload["parameter"] = object_name or ""

    elif agent_type == "object-validator":
        payload["parameter"] = f"validate {object_name}" if object_name else "validate all"

    elif agent_type == "text-extractor":
        pass  # no extra parameters needed

    elif agent_type == "damage-detector":
        payload["parameter"] = "detect damage"

    elif agent_type == "cleanliness-score":
        payload["parameter"] = "evaluate cleanliness"

    elif agent_type == "receipt-processor":
        payload["parameter"] = "process receipt"

    else:
        return ":grey_question: Unknown agent type"

    print(f"📤 Sending to Tiliter API for agent: {agent_type}")
    response = requests.post(
        TILITER_URL.replace("object-counter", agent_type),
        headers={
            'X-API-Key': TILITER_API_KEY,
            'Content-Type': 'application/json'
        },
        json=payload
    )

    if response.status_code != 200:
        return f":x: Tiliter API error {response.status_code}: {response.text}"

    try:
        result_json = response.json()
        return format_agent_response(agent_type, result_json)
    except Exception as e:
        return f":x: Could not parse Tiliter response:\n{str(e)}"

def post_to_slack(channel, thread_ts, message):
    print("💬 Posting result back to Slack...")
    requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={
            'Authorization': f'Bearer {SLACK_TOKEN}',
            'Content-Type': 'application/json'
        },
        json={
            'channel': channel,
            'thread_ts': thread_ts,
            'text': message
        }
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
