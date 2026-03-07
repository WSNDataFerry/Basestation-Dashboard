import socket
import time
import json
import threading
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import sqlite3
import os
from std_srvs.srv import Trigger

from drone_mission_interfaces.srv import MissionSelect, MissionStatus

class BasestationNode(Node):
    def __init__(self):
        super().__init__('basestation_node')
        self.publisher_ = self.create_publisher(String, 'topic_name', 10)

        self.mission_payload = {}

        self.launch_client = self.create_client(Trigger, '/waypoint_mission/launch')
        self.mission_select_client = self.create_client(MissionSelect, '/mission_manager/mission_select')

        self.mode_sub = self.create_subscription(
            String,
            '/wsn_offboarding/data_terminal',
            self.data_terminal_callback,
            10
        )

        # Setup DB connection for ingesting time-series records
        db_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Basestation-Dashboard', 'data')
        db_file = os.path.join(db_dir, 'basestation.db')
        os.makedirs(db_dir, exist_ok=True)
        self.db_path = os.path.abspath(db_file)
        # Ensure tables exist by initializing from the nodes_config.json
        cfg_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Basestation-Dashboard', 'data', 'nodes_config.json')
        # Basic sqlite initialization: create tables per node using config
        try:
            self.node_config = {}
            self.mac_to_node = {}
            self.id_to_node = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r') as f:
                    cfg = json.load(f)
                # store cfg for later lookups
                self.node_config = cfg
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                cur = conn.cursor()
                for node_id, meta in cfg.items():
                    # create per-node table
                    cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{node_id}" (
                        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_id INTEGER,
                        seq INTEGER,
                        mac TEXT,
                        ts INTEGER,
                        t REAL,
                        h REAL,
                        p REAL,
                        q INTEGER,
                        eco2 INTEGER,
                        tvoc INTEGER,
                        mx REAL,
                        my REAL,
                        mz REAL,
                        a REAL,
                        payload TEXT,
                        received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)
                    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{node_id}_ts ON "{node_id}"(ts);')

                    # build reverse lookup maps if mac or device_id provided in config
                    if isinstance(meta, dict):
                        mac = meta.get('mac')
                        if isinstance(mac, str) and mac.strip():
                            norm = mac.replace(':', '').lower()
                            self.mac_to_node[norm] = node_id
                        dev_id = meta.get('device_id') or meta.get('id')
                        if dev_id is not None:
                            try:
                                self.id_to_node[int(dev_id)] = node_id
                            except Exception:
                                pass
                conn.commit()
                conn.close()
        except Exception as e:
            self.get_logger().error(f"Failed to initialize DB tables: {e}")

        

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind(('localhost', 65432))  # Bind to localhost and port 65432
        self.server_socket.listen(1)
        # Make accept non-blocking by using a timeout and accepting in the timer loop
        self.server_socket.settimeout(0.5)
        self.client_socket = None

        self.get_logger().info("Socket server started (listening on localhost:65432). Will accept connections asynchronously.")

        self.wait_for_services()
        self.timer = self.create_timer(1.0, self.timer_callback)

    def wait_for_services(self):
        self.get_logger().info('Waiting for MAVROS services...')
        services = [
            (self.launch_client, 'WaypointLaunch'),
            (self.mission_select_client, 'MissionSelect'),
        ]

        for client, name in services:
            while not client.wait_for_service(timeout_sec=1.0):
                self.get_logger().info(f'Waiting for {name} service...')

        self.get_logger().info('All MAVROS  and WSN services are available!')

    def data_terminal_callback(self, msg: String):
        """Handle incoming data from /wsn_offboarding/data_terminal topic.
        Expected payload is a JSON string mapping node IDs to their telemetry dicts,
        e.g. {"node_004": {...}, "node_002": {...}}
        """
        try:
            text = msg.data if isinstance(msg, String) else msg
            parsed = json.loads(text)
        except Exception as e:
            self.get_logger().error(f'data_terminal_callback: failed to parse JSON: {e}')
            return

        if isinstance(parsed, dict):
            # Case A: mapping of node labels -> payloads
            node_keys = [k for k in parsed.keys() if isinstance(k, str) and k.startswith('node_')]
            if node_keys:
                for key in node_keys:
                    val = parsed.get(key)
                    if isinstance(val, dict):
                        self._insert_node_record(key, val)
                return

            # Case B: single-record telemetry with 'mac' or 'id'
            # normalize mac and lookup node label
            mac = parsed.get('mac') or parsed.get('MAC')
            dev_id = parsed.get('id') or parsed.get('device_id')
            node_label = None
            if mac:
                norm = str(mac).replace(':', '').lower()
                node_label = self.mac_to_node.get(norm)
            if node_label is None and dev_id is not None:
                try:
                    node_label = self.id_to_node.get(int(dev_id))
                except Exception:
                    node_label = None

            if node_label:
                self._insert_node_record(node_label, parsed)
            else:
                self.get_logger().warn('Received single-record telemetry but no matching node label found in nodes_config.json; ignoring')
        else:
            self.get_logger().info('data_terminal_callback: received non-dict payload; ignoring')

    def mission_select(self) -> bool:
        """Select the mission."""
        req = MissionSelect.Request()
        req.mission_type = "waypoint"
        req.payload_json = self.mission_payload

        future = self.mission_select_client.call_async(req)
        timeout = 5.0
        start_time = time.time()

        while not future.done():
            time.sleep(0.05)
            if time.time() - start_time > timeout:
                self.get_logger().info("[Base_station] timeout exceed!")
                return False
    
        if future.result() is not None:
            resp = future.result()
            if resp.accepted.lower() in ('true', 'yes', 'accepted', '1'):
                self.get_logger().info(f'[Base_station] Mission selected successfully: {resp.message}')
                return True
            else:
                self.get_logger().warn(f'[Base_station] Failed to select mission: {resp.message}')
                return False
        else:
            self.get_logger().error('[Base_station] Mission select service call failed!')
            return False
    
    def waypoint_launch(self) -> bool:
        """Launch the mission."""
        req = Trigger.Request()
        future = self.launch_client.call_async(req)
        timeout = 5.0
        start_time = time.time()

        while not future.done():
            time.sleep(0.05)
            if time.time() - start_time > timeout:
                self.get_logger().info("[Base_station] timeout exceed!")
                return False
    
        if future.result() is not None:
            if future.result().success:
                self.get_logger().info('[Base_station] Mission launched successfully')
                return True
            else:
                self.get_logger().warn('[Base_station] Failed to launch mission')
                return False
        else:
            self.get_logger().error('[Base_station] Mission launch service call failed!')
            return False
        
    def timer_callback(self):
        # Non-blocking accept: try to accept a client if none is connected
        if self.client_socket is None:
            try:
                client, addr = self.server_socket.accept()
                self.client_socket, self.client_address = client, addr
                self.get_logger().info(f"Connection established with {addr}")
            except socket.timeout:
                self.get_logger().debug("No incoming connection (accept timed out), will try again on next timer tick.")    
                pass
            except Exception as e:
                self.get_logger().debug(f"Accept error (non-fatal): {e}")

        if self.client_socket:
            try:
                data = self.client_socket.recv(1024)  # Receive up to 1024 bytes
                if data:
                    msg = data.decode('utf-8')
                    self.get_logger().info(f"Received: {msg}")
                    # Process the incoming mission JSON in a separate thread to avoid blocking
                    try:
                        threading.Thread(target=self._process_incoming_mission, args=(msg,), daemon=True).start()
                    except Exception as e:
                        self.get_logger().error(f"Failed to start mission handler thread: {e}")
            except Exception as e:
                self.get_logger().error(f"Error receiving data: {e}")
                try:
                    self.client_socket.close()
                except Exception:
                    pass
                self.client_socket = None

    def _process_incoming_mission(self, msg: str):
        """Validate incoming JSON, set the mission payload and call services to select and launch."""
        try:
            parsed = json.loads(msg)
            payload_str = json.dumps(parsed)
        except Exception as e:
            self.get_logger().error(f"Invalid JSON received, ignoring: {e}")
            return

        self.mission_payload = payload_str
        self.get_logger().info(f'[Base_station] mission payload set: {self.mission_payload}')
        self.get_logger().info(f'[Base_station] Attempting to select mission via service')

        try:
            ok = self.mission_select()
            if not ok:
                self.get_logger().error('[Base_station] mission_select failed; aborting launch')
                return
            self.get_logger().info('[Base_station] Mission selected, attempting launch')
            launched = self.waypoint_launch()
            if launched:
                self.get_logger().info('[Base_station] Mission launched successfully after socket receive')
            else:
                self.get_logger().error('[Base_station] Mission launch failed after socket receive')
        except Exception as e:
            self.get_logger().error(f'[Base_station] Exception while processing incoming mission: {e}')

    def _insert_node_record(self, node_id: str, record: dict):
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cur = conn.cursor()
            payload = json.dumps(record)
            device_id = record.get('id')
            seq = record.get('seq')
            mac = record.get('mac')
            ts = record.get('ts')
            t = record.get('t')
            h = record.get('h')
            p = record.get('p')
            q = record.get('q')
            eco2 = record.get('eco2')
            tvoc = record.get('tvoc')
            mx = record.get('mx')
            my = record.get('my')
            mz = record.get('mz')
            a = record.get('a')
            cur.execute(f'INSERT INTO "{node_id}" (device_id, seq, mac, ts, t, h, p, q, eco2, tvoc, mx, my, mz, a, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (device_id, seq, mac, ts, t, h, p, q, eco2, tvoc, mx, my, mz, a, payload))
            conn.commit()
            conn.close()
            # show the publisher works by re-publishing the original JSON string to the topic
            try:
                out_msg = String()
                out_msg.data = json.dumps({node_id: record})
                self.publisher_.publish(out_msg)
                self.get_logger().info(f'[WSN_DATA_OFFBOARD] Published data for {node_id}: {out_msg.data}')
            except Exception as e:
                self.get_logger().error(f'Failed to publish message after DB insert: {e}')
        except Exception as e:
            self.get_logger().error(f'Error inserting record into DB for {node_id}: {e}')

def main(args=None):
    rclpy.init(args=args)
    node = BasestationNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()