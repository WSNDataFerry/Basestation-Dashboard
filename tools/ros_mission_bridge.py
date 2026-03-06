#!/usr/bin/env python3
"""
Watch data/last_mission.json and call ROS2 services to start missions.

This script runs on the drone companion where ROS 2 is installed. It uses rclpy
to call the `/mission_manager/mission_select` (MissionSelect) service and then
optionally `/waypoint_mission/launch` Trigger service.

Usage: python3 ros_mission_bridge.py --file ../data/last_mission.json

Dependencies: ROS 2 Python packages must be available (rclpy, std_srvs).
"""
import argparse
import json
import os
import time
import threading

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--file', default=os.path.join(os.path.dirname(__file__), '..', 'data', 'last_mission.json'))
    p.add_argument('--poll', type=float, default=1.0, help='Poll interval seconds')
    return p.parse_args()


def run_bridge(mission_file, poll_interval):
    try:
        import rclpy
        from rclpy.node import Node
        from std_srvs.srv import Trigger
        from drone_mission_interfaces.srv import MissionSelect
    except Exception as e:
        print('Missing ROS 2 Python packages. Run this on the drone companion with ROS2 sourced.')
        raise

    class BridgeNode(Node):
        def __init__(self):
            super().__init__('ros_mission_bridge')
            self.cli = self.create_client(MissionSelect, '/mission_manager/mission_select')
            self.launch_cli = self.create_client(Trigger, '/waypoint_mission/launch')

        def wait_for_services(self, timeout=5.0):
            ok = self.cli.wait_for_service(timeout_sec=timeout)
            ok2 = self.launch_cli.wait_for_service(timeout_sec=timeout)
            return ok or ok2

        def call_mission_select(self, mission_type, payload_dict):
            req = MissionSelect.Request()
            req.mission_type = mission_type
            req.payload_json = json.dumps(payload_dict)
            fut = self.cli.call_async(req)
            rclpy.spin_until_future_complete(self, fut)
            return fut.result()

        def call_launch(self):
            req = Trigger.Request()
            fut = self.launch_cli.call_async(req)
            rclpy.spin_until_future_complete(self, fut)
            return fut.result()

    rclpy.init()
    node = BridgeNode()
    last_mtime = None
    print('ROS mission bridge started, watching', mission_file)
    try:
        while rclpy.ok():
            try:
                if os.path.exists(mission_file):
                    mtime = os.path.getmtime(mission_file)
                    if last_mtime is None or mtime > last_mtime:
                        last_mtime = mtime
                        with open(mission_file, 'r') as f:
                            obj = json.load(f)
                        mission_type = obj.get('mission_type')
                        payload = obj.get('payload')
                        if mission_type and payload:
                            print('Calling MissionSelect with', mission_type)
                            if not node.wait_for_services(timeout=10.0):
                                print('Mission services not available yet')
                            else:
                                try:
                                    resp = node.call_mission_select(mission_type, payload)
                                    print('MissionSelect response:', resp)
                                except Exception as e:
                                    print('MissionSelect call failed:', e)

                                # Try to call launch Trigger
                                try:
                                    print('Calling waypoint launch trigger')
                                    lresp = node.call_launch()
                                    print('Launch response:', lresp)
                                except Exception as e:
                                    print('Launch call failed:', e)
                time.sleep(poll_interval)
            except KeyboardInterrupt:
                break
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    args = parse_args()
    run_bridge(args.file, args.poll)
