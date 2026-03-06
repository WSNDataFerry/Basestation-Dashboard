import os
import json
from flask import Flask, jsonify, render_template, request, Response, stream_with_context
import threading
import queue

app = Flask(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
# New canonical log file (NDJSON lines) — renamed from mock_data.jsonl
LOG_FILE = os.path.join(DATA_DIR, 'log_data.json')

CONFIG_FILE = os.path.join(DATA_DIR, 'nodes_config.json')

@app.route('/')
def index():
    """Serves the main dashboard page."""
    return render_template('index.html')

@app.route('/api/config')
def get_config():
    """Serves the static cluster grouping and GPS map."""
    if not os.path.exists(CONFIG_FILE):
        return jsonify({"error": "Config file not found"}), 404
    try:
        with open(CONFIG_FILE, 'r') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/data')
def get_data():
    """
    Reads all NDJSON files (*.jsonl) in the data directory
    and returns a clean JSON array mapping to the frontend.
    """
    records = []

    try:
        # Construct the path and find the NDJSON file(s) we care about.
        # If the canonical `log_data.json` exists, use only that to avoid duplicates.
        log_path = os.path.join(DATA_DIR, 'log_data.json')
        if os.path.exists(log_path):
            jsonl_files = ['log_data.json']
        else:
            # Fall back to older .jsonl files if present
            jsonl_files = [f for f in os.listdir(DATA_DIR) if f.endswith('.jsonl')]

        if not jsonl_files:
            return jsonify({
                "status": "success",
                "count": 0,
                "data": []
            })

        for file_name in jsonl_files:
            file_path = os.path.join(DATA_DIR, file_name)
            # If the file is missing skip it
            if not os.path.exists(file_path):
                continue
            with open(file_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        # Parse each line as an individual JSON object (NDJSON)
                        obj = json.loads(line)
                        records.append(obj)
                    except json.JSONDecodeError:
                        print(f"Skipping malformed JSON line in {file_name}: {line}")

        return jsonify({
            "status": "success",
            "count": len(records),
            "data": records
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/data', methods=['POST'])
def post_data():
    """
    Accepts a JSON object or an array of JSON objects and appends them
    as NDJSON lines into the mock data file so the frontend (which polls
    /api/data) can pick up newly uploaded telemetry.
    """
    try:
        payload = request.get_json()
        if payload is None:
            return jsonify({"error": "Invalid or missing JSON body"}), 400

        entries = payload if isinstance(payload, list) else [payload]

        # Validate basic JSON objects (dictionaries)
        valid_entries = [e for e in entries if isinstance(e, dict)]
        if not valid_entries:
            return jsonify({"error": "No valid JSON objects to append"}), 400

        # Append each JSON object as a single NDJSON line to the canonical log file
        # Ensure the data directory exists
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, 'a') as f:
            for obj in valid_entries:
                f.write(json.dumps(obj) + '\n')

        # Broadcast to any SSE clients so connected browsers get real-time updates
        for obj in valid_entries:
            try:
                broadcast_event(obj)
            except Exception:
                pass

        return jsonify({"status": "success", "written": len(valid_entries)}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

app.config['TEMPLATES_AUTO_RELOAD'] = True

# Simple SSE broadcaster: list of queues for connected clients
_sse_clients = []
_sse_lock = threading.Lock()


def broadcast_event(obj):
    """Put obj into all client queues (non-blocking)."""
    with _sse_lock:
        for q in list(_sse_clients):
            try:
                q.put(obj, block=False)
            except Exception:
                # ignore full or closed queues
                pass


@app.route('/events')
def events():
    def gen():
        q = queue.Queue()
        with _sse_lock:
            _sse_clients.append(q)
        try:
            while True:
                data = q.get()
                yield f"data: {json.dumps(data)}\n\n"
        except GeneratorExit:
            # client disconnected
            with _sse_lock:
                try:
                    _sse_clients.remove(q)
                except ValueError:
                    pass

    return Response(stream_with_context(gen()), mimetype='text/event-stream')


@app.route('/api/ch_update', methods=['POST'])
def update_chs():
    """Update which nodes are cluster heads. Accepts JSON:{"chs": ["1001","2001"]}
    and writes the flag into `nodes_config.json` as `is_ch: true/false`.
    """
    try:
        body = request.get_json()
        if not body or 'chs' not in body:
            return jsonify({"error": "Missing 'chs' list in body"}), 400

        ch_list = set(str(x) for x in body['chs'])

        # Load current config
        if not os.path.exists(CONFIG_FILE):
            return jsonify({"error": "Config file not found"}), 404

        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)

        # Update entries
        for node_id, node in cfg.items():
            node['is_ch'] = (str(node_id) in ch_list)

        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=4)

        return jsonify({"status": "success", "written": len(ch_list)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/mission', methods=['POST'])
def post_mission():
    """Accepts mission requests from the frontend. Body: {"mission_type": "waypoint", "payload": {..}}
    Saves the mission to data/last_mission.json and, optionally, calls ROS commands if
    environment variable ENABLE_ROS_CALL=1 and ROS_MISSION_CMD/ROS_LAUNCH_CMD are provided.
    """
    try:
        body = request.get_json()
        if not body or 'mission_type' not in body or 'payload' not in body:
            return jsonify({"error": "Missing mission_type or payload"}), 400

        mission_type = body['mission_type']
        payload = body['payload']

        out_file = os.path.join(DATA_DIR, 'last_mission.json')
        with open(out_file, 'w') as f:
            json.dump({"mission_type": mission_type, "payload": payload}, f, indent=2)

        # Optionally call ROS service commands if enabled (use with care)
        enable_ros = os.environ.get('ENABLE_ROS_CALL', '0') == '1'
        ros_cmd = os.environ.get('ROS_MISSION_CMD')
        ros_launch_cmd = os.environ.get('ROS_LAUNCH_CMD')
        results = {"saved": out_file}

        if enable_ros and ros_cmd:
            # ros_cmd should be a format string with {mission_type} and {payload_json}
            try:
                cmd = ros_cmd.format(mission_type=mission_type, payload_json=json.dumps(payload))
                import subprocess
                proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                results['ros_call'] = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
            except Exception as e:
                results['ros_call_error'] = str(e)

        if enable_ros and ros_launch_cmd:
            try:
                cmd2 = ros_launch_cmd.format(mission_type=mission_type)
                import subprocess
                proc2 = subprocess.run(cmd2, shell=True, capture_output=True, text=True, timeout=30)
                results['ros_launch'] = {"returncode": proc2.returncode, "stdout": proc2.stdout, "stderr": proc2.stderr}
            except Exception as e:
                results['ros_launch_error'] = str(e)

        return jsonify({"status": "ok", "detail": results}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Run the server on all interfaces so the base station can access it locally
    print("Starting Base Station Dashboard on http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)
