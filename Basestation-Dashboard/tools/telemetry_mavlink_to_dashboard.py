#!/usr/bin/env python3

import time
import argparse
import logging
import sys
import requests
from pymavlink import mavutil

logging.basicConfig(level=logging.INFO,
format='[%(asctime)s] %(levelname)s: %(message)s')


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--port', default='/dev/ttyUSB0')
    p.add_argument('--baud', default=57600, type=int)
    p.add_argument('--url', default='http://localhost:5001/api/data')
    p.add_argument('--id', default='drone_1')
    return p.parse_args()


# request message streams
def request_streams(m):

    def req(msg_id, hz):
        interval = int(1e6 / hz)

        m.mav.command_long_send(
            m.target_system,
            m.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            msg_id,
            interval,
            0,0,0,0,0
        )

    req(mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 5)
    req(mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD, 5)
    req(mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT, 2)
    req(mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE, 10)
    req(mavutil.mavlink.MAVLINK_MSG_ID_HEARTBEAT, 1)

    logging.info("Requested telemetry streams")


def post_payload(url, payload):
    try:
        requests.post(url, json=payload, timeout=2)
    except:
        pass


def run(port, baud, url, drone_id):

    logging.info(f'Connecting {port} @ {baud}')

    m = mavutil.mavlink_connection(port, baud=baud)

    logging.info("Waiting for heartbeat...")
    m.wait_heartbeat()

    logging.info("Connected to system %d component %d",
                 m.target_system,
                 m.target_component)

    request_streams(m)

    while True:

        msg = m.recv_match(blocking=True)

        if not msg:
            continue

        msg_type = msg.get_type()
        ts = int(time.time())

        if msg_type == "GLOBAL_POSITION_INT":

            payload = {
                "id": drone_id,
                "type": "drone",
                "ts": ts,
                "lat": msg.lat / 1e7,
                "lon": msg.lon / 1e7,
                "alt": msg.alt / 1000,
                "relative_alt": msg.relative_alt / 1000,
                "heading": msg.hdg / 100 if msg.hdg != 65535 else None
            }

            post_payload(url, payload)


        elif msg_type == "VFR_HUD":

            payload = {
                "id": drone_id,
                "type": "drone",
                "ts": ts,
                "groundspeed": msg.groundspeed,
                "airspeed": msg.airspeed,
                "heading": msg.heading,
                "throttle": msg.throttle
            }

            post_payload(url, payload)


        elif msg_type == "GPS_RAW_INT":

            payload = {
                "id": drone_id,
                "type": "drone",
                "ts": ts,
                "lat": msg.lat / 1e7,
                "lon": msg.lon / 1e7,
                "alt": msg.alt / 1000,
                "satellites_visible": msg.satellites_visible
            }

            post_payload(url, payload)


        elif msg_type == "ATTITUDE":

            payload = {
                "id": drone_id,
                "type": "drone",
                "ts": ts,
                "roll": msg.roll * 57.2958,
                "pitch": msg.pitch * 57.2958,
                "yaw": msg.yaw * 57.2958
            }

            post_payload(url, payload)


        elif msg_type == "HEARTBEAT":

            payload = {
                "id": drone_id,
                "type": "drone",
                "ts": ts,
                "base_mode": msg.base_mode,
                "custom_mode": msg.custom_mode,
                "system_status": msg.system_status
            }

            post_payload(url, payload)


if __name__ == "__main__":

    args = parse_args()

    try:
        run(args.port, args.baud, args.url, args.id)
    except KeyboardInterrupt:
        print("Stopping")
        sys.exit()