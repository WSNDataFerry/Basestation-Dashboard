#!/usr/bin/env python3

import argparse
import time
import requests
from pymavlink import mavutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:5001/api/data")
    parser.add_argument("--id", default="drone_1")
    parser.add_argument("--speed", type=float, default=20)

    args = parser.parse_args()

    print("Opening BIN log:", args.log)

    log = mavutil.mavlink_connection(
        args.log,
        dialect="ardupilotmega",
        notimestamps=True
    )

    while True:

        msg = log.recv_match(type=['GPS'], blocking=False)

        if msg is None:
            break

        lat = getattr(msg, "Lat", None)
        lon = getattr(msg, "Lng", None)
        alt = getattr(msg, "Alt", None)

        if lat is None or lon is None:
            continue

        lat = lat / 1e7
        lon = lon / 1e7

        payload = {
            "id": args.id,
            "type": "drone",
            "ts": int(time.time()),
            "lat": lat,
            "lon": lon,
            "alt": alt
        }

        try:
            requests.post(args.url, json=payload)
            print("Sent:", lat, lon)
        except Exception as e:
            print("POST failed:", e)

        time.sleep(1 / args.speed)


if __name__ == "__main__":
    main()