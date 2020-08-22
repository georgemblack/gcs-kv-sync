import json
import sys
import base64
import os
import requests
from flask import Flask, request
from google.cloud import storage

CF_API_ENDPOINT = "https://api.cloudflare.com/client/v4"
CF_API_EMAIL = os.environ["CLOUDFLARE_API_EMAIL"]
CF_API_KEY = os.environ["CLOUDFLARE_API_EMAIL"]

CF_KV_NAMESPACE_ID = os.environ["CLOUDFLARE_KV_NAMESPACE_ID"]
CF_ACCOUNT_ID = os.environ["CLOUDFLARE_ACCOUNT_ID"]

app = Flask(__name__)


@app.route("/", methods=["POST"])
def index():
    envelope = request.get_json()
    if not envelope:
        print("Error: No Pub/Sub message received")
        return "Bad Request: No Pub/Sub message received", 400

    if not isinstance(envelope, dict) or "message" not in envelope:
        print("Error: Invalid Pub/Sub message format")
        return "Bad Request: Invalid Pub/Sub message format", 400

    message = envelope["message"]

    if (
        not isinstance(message, dict)
        or not message["data"]
        or not message["attributes"]
    ):
        print("Error: Invalid Pub/Sub message format")
        return "Bad Request: Invalid Pub/Sub message format", 400

    event_type = message["attributes"]["eventType"]
    if event_type != "OBJECT_FINALIZE":
        print(f"Ignoring unrelated Cloud Storage event: {event_type}")
        return "No action taken", 200

    try:
        data = json.loads(base64.b64decode(message["data"]).decode())
    except Exception as e:
        print("Error: Data property is not valid base64 encoded JSON")
        return "Bad Request: Data property is not valid base64 encoded JSON", 400

    if not data["name"] or not data["bucket"]:
        print(f"Error: Expected name/bucket in notification")
        return f"Bad Request: Expected name/bucket in notification", 400

    try:
        upload_to_kv(data)
        sys.stdout.flush()

    except Exception as e:
        print(f"Error: {e}")
        return ("", 500)

    return ("Uploaded to Workers KV", 200)


def upload_to_kv(data):
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(data["bucket"])
    blob = bucket.get_blob(data["name"])

    # build kv req
    kv_key = f"{data['bucket']}/{data['name']}"
    kv_value = blob.download_as_string()
    kv_metadata = build_kv_metadata(data["name"])

    url = f"{CF_API_ENDPOINT}/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/values/{kv_key}"
    headers = {"X-Auth-Email": CF_API_EMAIL, "X-Auth-Key": CF_API_KEY}
    payload = {"value": kv_value, "metadata": json.dumps(kv_metadata)}

    # upload to kv
    response = requests.put(url, headers=headers, files=payload)
    response.raise_for_status()
    response_body = response.json()
    print(f"CF API response: {response_body}")


def build_kv_metadata(object_name):
    return {"cacheControl": get_cache_control(object_name)}


def get_cache_control(object_name):
    seconds = "3600"
    extension = object_name.split(".").pop()

    if extension in ["jpg", "jpeg", "png", "webp", "mov", "ico", "svg", "webmanifest"]:
        seconds = "2592000"  # 30 days
    elif extension in ["js", "css"]:
        seconds = "172800"  # 2 days
    elif extension in ["xml", "json"]:
        seconds = "900"

    cache_control = f"public, max-age={seconds}"
    print(f"Calculated cache-control: {cache_control}")
    return cache_control
