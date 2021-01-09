import json
import sys
import base64
import os
import requests
from flask import Flask, request
from google.cloud import storage

SOURCE_BUCKETS = ["george.black", "media.george.black"]

CF_API_ENDPOINT = "https://api.cloudflare.com/client/v4"
CF_API_EMAIL = os.environ["CF_API_EMAIL"]
CF_API_TOKEN = os.environ["CF_API_TOKEN"]
CF_KV_NAMESPACE_ID = os.environ["CF_KV_NAMESPACE_ID"]
CF_ACCOUNT_ID = os.environ["CF_ACCOUNT_ID"]

MIME_TYPES_MAP = {
    "aac": "audio/aac",
    "arc": "application/x-freearc",
    "avi": "video/x-msvideo",
    "css": "text/css",
    "csv": "text/csv",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "gz": "application/gzip",
    "gpx": "application/gpx+xml",
    "gif": "image/gif",
    "html": "text/html",
    "ico": "image/vnd.microsoft.icon",
    "ics": "text/calendar",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "js": "text/javascript",
    "json": "application/json; charset=utf-8",
    "mid": "audio/x-midi",
    "midi": "audio/x-midi",
    "mpeg": "video/mpeg",
    "png": "image/png",
    "pdf": "application/pdf",
    "rar": "application/vnd.rar",
    "rtf": "application/rtf",
    "sh": "application/x-sh",
    "svg": "image/svg+xml",
    "tar": "application/x-tar",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "txt": "text/plain",
    "usdz": "model/usd",
    "wav": "audio/wav",
    "weba": "audio/webm",
    "webm": "video/webm",
    "webp": "image/webp",
    "xhtml": "application/xhtml+xml",
    "xml": "application/xml",
    "zip": "application/zip",
}

app = Flask(__name__)
storage_client = storage.Client()


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

    attributes = message["attributes"]
    event_type = attributes["eventType"]
    if event_type not in ["OBJECT_FINALIZE", "OBJECT_DELETE"]:
        print(f"Ignoring unrelated Cloud Storage event type: {event_type}")
        return "No action required", 200

    if event_type == "OBJECT_DELETE" and "overwrittenByGeneration" in attributes.keys():
        print(f"Ignoring OBJECT_DELETE event for overwritten object")
        return "No action required", 200

    try:
        data = json.loads(base64.b64decode(message["data"]).decode())
    except Exception as e:
        print("Error: Data property is not valid base64 encoded JSON")
        return "Bad Request: Data property is not valid base64 encoded JSON", 400

    if not data["name"] or not data["bucket"]:
        print(f"Error: Expected name/bucket in notification")
        return f"Bad Request: Expected name/bucket in notification", 400

    if data["bucket"] not in SOURCE_BUCKETS:
        print(f"Ignoring event from bucket {data['bucket']}")
        return ("", 200)

    try:
        if event_type == "OBJECT_FINALIZE":
            handle_object_finalize(data)
        if event_type == "OBJECT_DELETE":
            handle_object_delete(data)
        sys.stdout.flush()

    except Exception as e:
        print(f"Error: {e}")
        return ("", 500)

    return ("Uploaded to Workers KV", 200)


def handle_object_finalize(data):
    """
    Upload to Cloudflare Workers KV.
    """
    bucket = storage_client.get_bucket(data["bucket"])
    blob = bucket.get_blob(data["name"])

    print(f"Adding object to KV: {data['name']}")

    kv_key = f"{data['bucket']}/{data['name']}"
    kv_value = blob.download_as_string()
    kv_metadata = build_kv_metadata(data["name"])

    url = f"{CF_API_ENDPOINT}/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/values/{kv_key}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    payload = {"value": kv_value, "metadata": json.dumps(kv_metadata)}

    response = requests.put(url, headers=headers, files=payload)
    response.raise_for_status()
    response_body = response.json()
    print(f"CF API response: {response_body}")


def handle_object_delete(data):
    """
    Delete from Cloudflare Workers KV.
    """
    print(f"Removing object from KV: {data['name']}")

    kv_key = f"{data['bucket']}/{data['name']}"
    url = f"{CF_API_ENDPOINT}/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/values/{kv_key}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}

    response = requests.delete(url, headers=headers)
    response.raise_for_status()
    response_body = response.json()
    print(f"CF API response: {response_body}")


def build_kv_metadata(object_name):
    metadata = {
        "cacheControl": get_cache_control(object_name),
        "mimeType": get_content_type(object_name),
    }
    print(f"Metadata for {object_name}: {metadata}")
    return metadata


def get_cache_control(object_name):
    seconds = "2592000"
    extension = object_name.split(".").pop()

    if extension in ["html", "xml", "json", "txt"]:
        seconds = "900"
    elif extension in ["js", "css"]:
        seconds = "172800"

    return f"public, max-age={seconds}"


def get_content_type(object_name):
    extension = object_name.split(".").pop()
    if extension in MIME_TYPES_MAP.keys():
        return MIME_TYPES_MAP[extension]
    return "application/octet-stream"
