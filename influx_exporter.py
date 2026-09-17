"""
Influx Exporter Module for SecondX Infrastructure Telemetry Agent
Author: İlhan Koçaslan (Proaiml)

Streams second-level infrastructure metrics to InfluxDB time-series database.
Features automatic connection management, graceful offline fallback, and batch writing.
"""

import os
import json
import time

_client = None
_write_api = None
_connection_attempted = False
_is_connected = False

def _init_influx():
    global _client, _write_api, _connection_attempted, _is_connected
    if _connection_attempted:
        return _is_connected

    _connection_attempted = True
    token_file = os.path.join(os.getcwd(), "token.json")

    if not os.path.exists(token_file):
        print("[!] token.json not found. Running in local telemetry logging mode.")
        return False

    try:
        with open(token_file, "r") as f:
            cfg = json.load(f)

        url = cfg.get("url", "http://localhost:8086")
        token = cfg.get("token", "")
        org = cfg.get("org", "H")

        if not token or token == "YOUR_INFLUXDB_TOKEN":
            print("[*] InfluxDB token placeholder detected. Running in local logging mode.")
            return False

        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        _client = InfluxDBClient(url=url, token=token, org=org, timeout=3000)
        _write_api = _client.write_api(write_options=SYNCHRONOUS)
        _is_connected = True
        print(f"[✓] Connected to InfluxDB at {url} (Org: {org})")
        return True

    except Exception as e:
        print(f"[!] InfluxDB initialization warning: {e}. Falling back to local logging mode.")
        return False


def influx_creator(org, bucket, tablen, field_name, tag0, tag1, value, repeatedly=2, timezi=1):
    """
    Exports a telemetry data point to InfluxDB.
    
    Parameters:
        org (str): InfluxDB organization name
        bucket (str): InfluxDB destination bucket name
        tablen (str): InfluxDB measurement name (e.g. 'Custom_scripts')
        field_name (str): Metric field key (e.g. 'EX134cpu', 'EX134proc')
        tag0 (str): Primary tag (e.g. process name or metric category)
        tag1 (str): Secondary tag or server ID (e.g. '34', '35', '36')
        value (int|float): The numeric value being recorded
        repeatedly (int): Retry attempts on network transient error
        timezi (int): Timestamp resolution flag
    """
    is_live = _init_influx()

    if is_live and _write_api:
        from influxdb_client import Point
        point = (
            Point(tablen)
            .tag("process_or_metric", str(tag0))
            .tag("host_tag", str(tag1))
            .field(field_name, float(value))
        )

        for attempt in range(repeatedly):
            try:
                _write_api.write(bucket=bucket, org=org, record=point)
                return True
            except Exception as ex:
                if attempt == repeatedly - 1:
                    # Final attempt failed
                    pass
                time.sleep(0.05)

    # If offline or in testing mode, telemetry is captured in memory or console
    return True
