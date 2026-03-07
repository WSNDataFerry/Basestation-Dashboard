import os
import json
from flask import Flask, jsonify, render_template, request, Response, stream_with_context
import threading
import queue
import socket
import sqlite3
import db as dashboard_db

app = Flask(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
# New canonical log file (NDJSON lines) — renamed from mock_data.jsonl
LOG_FILE = os.path.join(DATA_DIR, 'log_data.jsonl')

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
    # Prefer DB-backed data if available
    try:
        db_path = os.path.join(os.path.dirname(__file__), 'data', 'basestation.db')
        if os.path.exists(db_path):
            # read all configured nodes and return recent samples
            try:
                cfg = {}
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, 'r') as f:
                        cfg = json.load(f)
                records = []
                for node_id in cfg.keys():
                    recs = dashboard_db.query_recent(node_id, limit=50, db_path=db_path)
                    for r in recs:
                        # normalize output to match previous NDJSON shape when possible
                        out = r.get('payload') if isinstance(r.get('payload'), dict) else {}
                        out.update({
                            "_rowid": r.get('rowid'),
                            "_received_at": r.get('received_at')
                        })
                        records.append(out)
                # Also include drone telemetry records from log files (DB only stores WSN node data)
                for log_name in ['log_data.jsonl', 'log_data.json']:
                    candidate = os.path.join(DATA_DIR, log_name)
                    if os.path.exists(candidate):
                        with open(candidate, 'r') as lf:
                            for line in lf:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    obj = json.loads(line)
                                    if obj.get('type') == 'drone' and obj.get('lat') is not None:
                                        records.append(obj)
                                except json.JSONDecodeError:
                                    pass
                        break  # use the first log file found
                return jsonify({"status": "success", "count": len(records), "data": records})
            except Exception as e:
                return jsonify({"error": f"DB read error: {e}"}), 500

        # Fallback to file-based NDJSON if DB not present
        records = []
        # Construct the path and find the NDJSON file(s) we care about.
        # If the canonical `log_data.json` exists, use only that to avoid duplicates.
        log_path = os.path.join(DATA_DIR, 'log_data.json')
        if os.path.exists(log_path):
            jsonl_files = ['log_data.json']
        else:
            # Fall back to older .jsonl files if present
            jsonl_files = [f for f in os.listdir(DATA_DIR) if f.endswith('.jsonl')]

        if not jsonl_files:
            return jsonify({"status": "success", "count": 0, "data": []})

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

        return jsonify({"status": "success", "count": len(records), "data": records})
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


@app.route('/api/send_selected', methods=['POST'])
def send_selected():
    """Accepts JSON: {"selected": ["node_002","node_004"], "nodes": {<mapping>} }
    Builds an ordered payload containing only the selected nodes (in the specified order)
    and forwards it to the local TCP socket server at 127.0.0.1:65432. Returns status info.
    """
    try:
        body = request.get_json()
        if not body or 'selected' not in body or 'nodes' not in body:
            return jsonify({"error": "Missing 'selected' list or 'nodes' mapping in body"}), 400

        selected = body['selected']
        nodes = body['nodes']

        # Validate types
        if not isinstance(selected, list) or not isinstance(nodes, dict):
            return jsonify({"error": "'selected' must be a list and 'nodes' must be a mapping"}), 400

        # Build ordered payload preserving the provided order and only including existing nodes
        ordered = {}
        for node_id in selected:
            if node_id in nodes:
                ordered[node_id] = nodes[node_id]

        if not ordered:
            return jsonify({"error": "No matching nodes found for selection"}), 400

        # Send to socket server
        results = {}
        try:
            sock_msg = json.dumps(ordered) + "\n"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect(('127.0.0.1', 65432))
                s.sendall(sock_msg.encode('utf-8'))
            results['socket_send'] = 'ok'
        except Exception as e:
            results['socket_send_error'] = str(e)

        # Optionally persist the last mission for debugging
        try:
            out_file = os.path.join(DATA_DIR, 'last_selected.json')
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(out_file, 'w') as f:
                json.dump(ordered, f, indent=2)
            results['saved'] = out_file
        except Exception:
            pass

        return jsonify({"status": "ok", "detail": results}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Run the server on all interfaces so the base station can access it locally
    # Ensure DB tables exist for configured nodes
    try:
        if os.path.exists(CONFIG_FILE):
            try:
                dashboard_db.init_db_from_config(CONFIG_FILE)
            except Exception as e:
                print(f"Warning: failed to init DB from config: {e}")
    except Exception:
        pass

    print("Starting Base Station Dashboard on http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)
