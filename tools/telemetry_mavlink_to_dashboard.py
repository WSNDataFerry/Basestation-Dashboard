#!/usr/bin/env python3
"""
Read MAVLink telemetry from a serial telemetry radio (USB) and POST
simple telemetry JSON objects to the base station dashboard's
`/api/data` endpoint so the drone appears on the map.

Usage:
  python3 telemetry_mavlink_to_dashboard.py --port /dev/ttyUSB0 --baud 57600 --id drone_1

Dependencies:
  pip install pymavlink requests

The script listens for GLOBAL_POSITION_INT and VFR_HUD messages and
constructs small JSON telemetry records with lat/lon/alt/heading/groundspeed
and a timestamp, then POSTs them to the dashboard.
"""
import time
import argparse
import json
import sys
import logging

try:
    from pymavlink import mavutil
except Exception:
    print("Missing dependency: pymavlink. Install with: pip install pymavlink")
    raise

try:
    import requests
except Exception:
    print("Missing dependency: requests. Install with: pip install requests")
    raise

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')


def parse_args():
    p = argparse.ArgumentParser(description='Forward MAVLink telemetry to Base Station Dashboard')
    p.add_argument('--port', '-p', help='Serial port (e.g. /dev/ttyUSB0). If omitted tries common ports', default=None)
    p.add_argument('--baud', '-b', type=int, default=57600, help='Serial baud rate (default: 57600)')
    p.add_argument('--url', '-u', default='http://localhost:5001/api/data', help='Dashboard POST URL')
    p.add_argument('--id', default='drone_1', help='Identifier used in telemetry records')
    p.add_argument('--autoreconnect', action='store_true', help='Try to reconnect on serial disconnect')
    return p.parse_args()


COMMON_PORTS = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1', '/dev/serial0']


def pick_port(user_port=None):
    if user_port:
        return user_port
    for p in COMMON_PORTS:
        try:
            with open(p):
                return p
        except Exception:
            continue
    return COMMON_PORTS[0]


def run(port, baud, url, ident, autoreconnect=False):
    logging.info('Starting MAVLink -> Dashboard bridge')
    last_hb = time.time()

    while True:
        try:
            logging.info(f'Connecting to MAVLink port {port} @ {baud}')
            m = mavutil.mavlink_connection(port, baud=baud)

            # Wait for a heartbeat to confirm link
            logging.info('Waiting for heartbeat...')
            m.wait_heartbeat(timeout=10)
            logging.info('Heartbeat received, streaming telemetry')

            while True:
                msg = m.recv_match(blocking=True, timeout=5)
                if msg is None:
                    # No message in timeout period, continue
                    continue

                mtype = msg.get_type()
                now_ts = int(time.time())

                # Prefer high-precision global position
                if mtype == 'GLOBAL_POSITION_INT':
                    try:
                        lat = msg.lat / 1e7
                        lon = msg.lon / 1e7
                        alt = msg.alt / 1000.0  # mm -> m
                        rel_alt = msg.relative_alt / 1000.0
                        heading = msg.hdg / 100.0 if hasattr(msg, 'hdg') and msg.hdg is not None else None
                        payload = {
                            'id': ident,
                            'type': 'drone',
                            'ts': now_ts,
                            'lat': lat,
                            'lon': lon,
                            'alt': alt,
                            'relative_alt': rel_alt,
                            'heading': heading
                        }
                        post_payload(url, payload)
                    except Exception as e:
                        logging.debug('Error processing GLOBAL_POSITION_INT: %s', e)

                # VFR_HUD contains groundspeed/airspeed/heading
                elif mtype == 'VFR_HUD':
                    try:
                        payload = {
                            'id': ident,
                            'type': 'drone',
                            'ts': now_ts,
                            'groundspeed': float(getattr(msg, 'groundspeed', 0.0)),
                            'airspeed': float(getattr(msg, 'airspeed', 0.0)),
                            'heading': float(getattr(msg, 'heading', 0.0)),
                            'throttle': float(getattr(msg, 'throttle', 0.0))
                        }
                        post_payload(url, payload)
                    except Exception as e:
                        logging.debug('Error processing VFR_HUD: %s', e)

                # GPS_RAW_INT as fallback if GLOBAL_POSITION_INT isn't available
                elif mtype == 'GPS_RAW_INT':
                    try:
                        lat = msg.lat / 1e7 if hasattr(msg, 'lat') else None
                        lon = msg.lon / 1e7 if hasattr(msg, 'lon') else None
                        alt = msg.alt / 1000.0 if hasattr(msg, 'alt') else None
                        payload = {
                            'id': ident,
                            'type': 'drone',
                            'ts': now_ts,
                            'lat': lat,
                            'lon': lon,
                            'alt': alt,
                            'satellites_visible': getattr(msg, 'satellites_visible', None)
                        }
                        post_payload(url, payload)
                    except Exception as e:
                        logging.debug('Error processing GPS_RAW_INT: %s', e)

                # Heartbeat — can be used to detect mode or system status
                elif mtype == 'HEARTBEAT':
                    try:
                        # autopilot, type, base_mode, system_status are available
                        payload = {
                            'id': ident,
                            'type': 'drone',
                            'ts': now_ts,
                            'base_mode': getattr(msg, 'base_mode', None),
                            'custom_mode': getattr(msg, 'custom_mode', None),
                            'system_status': getattr(msg, 'system_status', None)
                        }
                        post_payload(url, payload)
                    except Exception as e:
                        logging.debug('Error processing HEARTBEAT: %s', e)

                # ATTITUDE contains roll, pitch, yaw (radians)
                elif mtype == 'ATTITUDE':
                    try:
                        # convert radians to degrees for frontend convenience
                        roll = float(getattr(msg, 'roll', 0.0)) * 180.0 / 3.141592653589793
                        pitch = float(getattr(msg, 'pitch', 0.0)) * 180.0 / 3.141592653589793
                        yaw = float(getattr(msg, 'yaw', 0.0)) * 180.0 / 3.141592653589793
                        payload = {
                            'id': ident,
                            'type': 'drone',
                            'ts': now_ts,
                            'roll': round(roll, 2),
                            'pitch': round(pitch, 2),
                            'yaw': round(yaw, 2)
                        }
                        post_payload(url, payload)
                    except Exception as e:
                        logging.debug('Error processing ATTITUDE: %s', e)

        except Exception as e:
            logging.error('Connection error: %s', e)
            if not autoreconnect:
                logging.info('Exiting (autoreconnect disabled)')
                sys.exit(1)
            logging.info('Retrying in 5s...')
            time.sleep(5)


def post_payload(url, payload):
    try:
        # Use requests to POST JSON to the dashboard
        resp = requests.post(url, json=payload, timeout=5)
        if resp.status_code not in (200, 201):
            logging.warning('Dashboard returned %s: %s', resp.status_code, resp.text)
        else:
            logging.info('Posted telemetry: %s', payload)
    except Exception as e:
        logging.debug('Failed to POST telemetry: %s', e)


if __name__ == '__main__':
    args = parse_args()
    port = pick_port(args.port)
    logging.info('Using port: %s', port)
    run(port, args.baud, args.url, args.id, autoreconnect=args.autoreconnect)
