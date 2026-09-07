"""Read one production replay and probe its private HLS without modifying it.

Requires authenticated Railway CLI, psycopg2, boto3 and ffprobe. Credentials and
signed input URLs stay in memory and are never printed.
"""
import argparse
import base64
import json
import posixpath
import re
import subprocess
import urllib.request

import boto3
import psycopg2
from botocore.config import Config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("live_id", type=int)
    args = parser.parse_args()
    variables = json.loads(subprocess.check_output(["railway", "variables", "--service", "coinpilotx-media-engine", "--json"]))
    database = json.loads(subprocess.check_output(["railway", "variables", "--service", "Postgres", "--json"]))
    with psycopg2.connect(database["DATABASE_PUBLIC_URL"], connect_timeout=10, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agora_recording_prefix,agora_recording_filename,mux_recording_asset_id,ended_at,recording_status FROM pulse_live_sessions WHERE id=%s", (args.live_id,))
            prefix, filename, asset_id, ended_at, status = cur.fetchone()
    client = boto3.client("s3", endpoint_url=variables["R2_ENDPOINT_URL"], aws_access_key_id=variables["R2_ACCESS_KEY_ID"], aws_secret_access_key=variables["R2_SECRET_ACCESS_KEY"], config=Config(signature_version="s3v4"))
    key = filename if filename.startswith(prefix + "/") else prefix + "/" + filename
    body = client.get_object(Bucket=variables["R2_BUCKET"], Key=key)["Body"].read().decode()
    lines = []
    for line in body.splitlines():
        if line and not line.startswith("#"):
            line = client.generate_presigned_url("get_object", Params={"Bucket": variables["R2_BUCKET"], "Key": posixpath.join(posixpath.dirname(key), line)}, ExpiresIn=300)
        lines.append(line)
    result = subprocess.run(["ffprobe", "-v", "error", "-f", "hls", "-protocol_whitelist", "pipe,https,tcp,tls,crypto", "-i", "pipe:0", "-show_entries", "stream=codec_name,codec_type,width,height:format=duration", "-of", "json"], input="\n".join(lines) + "\n", text=True, capture_output=True, timeout=45)
    errors = re.sub(r"https?://[^\s'\"]+", "[REDACTED_URL]", result.stderr)
    print(json.dumps({"live_id": args.live_id, "ended_at": ended_at, "recording_status": status, "probe_exit": result.returncode, "probe": json.loads(result.stdout or "{}"), "probe_errors": errors}, default=str))
    ingest_key = posixpath.join(posixpath.dirname(key), "mux-ingest.m3u8")
    ingest_url = client.generate_presigned_url("get_object", Params={"Bucket": variables["R2_BUCKET"], "Key": ingest_key}, ExpiresIn=300)
    root_probe = subprocess.run(["ffprobe", "-v", "error", "-i", ingest_url, "-show_entries", "format=format_name", "-of", "json"], text=True, capture_output=True, timeout=45)
    print(json.dumps({"stored_ingest_probe_exit": root_probe.returncode, "errors": re.sub(r"https?://[^\s'\"]+", "[REDACTED_URL]", root_probe.stderr)}))
    auth = base64.b64encode((variables["MUX_TOKEN_ID"] + ":" + variables["MUX_TOKEN_SECRET"]).encode()).decode()
    request = urllib.request.Request("https://api.mux.com/video/v1/assets/" + asset_id, headers={"Authorization": "Basic " + auth})
    asset = json.load(urllib.request.urlopen(request, timeout=15))["data"]
    print(json.dumps({"asset_id": asset_id, "mux_status": asset.get("status"), "errors": asset.get("errors"), "created_at": asset.get("created_at")}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Subprocess timeout exceptions can include signed command arguments.
        print(json.dumps({"diagnostic_error_type": type(exc).__name__}))
        raise SystemExit(1) from None
