from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from datetime import datetime
import json, os, difflib, time
import random
from pathlib import Path
from filelock import FileLock
from apscheduler.schedulers.background import BackgroundScheduler
import shutil

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# === Configuration ===
INPUT_FILE = "/mnt/data/input/arxiv_2000_2025_all_final.jsonl"
USER_LOG_DIR = "user_logs"
os.makedirs(USER_LOG_DIR, exist_ok=True)

MODELS = ["gemini_flash", "grok", "chatgpt_4o_mini", "claude", "copilot"]

USERS_FILE = "users.json"
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, "w") as f:
        json.dump({}, f)


# === Ensure shared input file is present ===
def ensure_initial_data():
    os.makedirs("/mnt/data/input", exist_ok=True)
    target = "/mnt/data/input/arxiv_2000_2025_all_final.jsonl"
    if not os.path.exists(target):
        os.makedirs("inputs", exist_ok=True)
        shutil.copy("inputs/arxiv_2000_2025_all_final.jsonl", target)

ensure_initial_data()


# === Helper to assign next prompt ===
def get_next_prompt(model_name: str, user_id: str) -> dict:
    input_file = INPUT_FILE
    user_log_file = os.path.join(USER_LOG_DIR, f"{model_name}_users.jsonl")
    lock_file = f"{user_log_file}.lock"

    with FileLock(lock_file):
        if not os.path.exists(input_file):
            return {"error": "Prompt file not found."}

        with open(input_file, 'r', encoding='utf-8') as f:
            prompts = [json.loads(line) for line in f]

        assigned_indices = set()
        user_map = {}

        if os.path.exists(user_log_file):
            with open(user_log_file, 'r', encoding='utf-8') as log:
                for line in log:
                    entry = json.loads(line)
                    assigned_indices.add(entry['index'])
                    user_map[entry['index']] = entry

        for i, prompt in enumerate(prompts):
            if i not in assigned_indices:
                log_entry = {
                    "user_id": user_id,
                    "index": i,
                    "title": prompt.get("title", ""),
                    "assigned_at": int(time.time()),
                    "submitted": False
                }
                with open(user_log_file, 'a', encoding='utf-8') as log:
                    log.write(json.dumps(log_entry) + '\n')
                prompt["index_assigned"] = i
                prompt["model"] = model_name
                prompt["note"] = f"Prompt #{i} assigned."
                return prompt

        return {"error": f"All prompts exhausted for model: {model_name}"}


# === Authentication Routes ===
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])

        with open(USERS_FILE, 'r+') as f:
            users = json.load(f)
            if username in users:
                return "Username already exists!"
            users[username] = password
            f.seek(0)
            json.dump(users, f)
            f.truncate()

        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        with open(USERS_FILE, 'r') as f:
            users = json.load(f)
            if username in users and check_password_hash(users[username], password):
                session['username'] = username
                return redirect(url_for('user_dashboard'))
            else:
                return "Invalid credentials!"

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))


# === Main Dashboard ===
@app.route('/')
def home():
    return render_template('index.html')


@app.route('/user_dashboard')
def user_dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('user_dashboard.html', username=session['username'], models=MODELS)


# === Submit Prompt Response ===
@app.route('/submit/<model>', methods=['GET', 'POST'])
def submit(model):
    if 'username' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        data = request.form.to_dict()
        model_name = model
        output_file = f"/mnt/data/output_{model_name}.jsonl"
        data["user"] = session['username']
        data["submitted_at"] = int(time.time())

        # Mark as submitted in user log
        log_file = os.path.join(USER_LOG_DIR, f"{model_name}_users.jsonl")
        updated_lines = []
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    if record["user_id"] == session['username'] and record["index"] == int(data.get("index_assigned", -1)):
                        record["submitted"] = True
                    updated_lines.append(json.dumps(record))

            with open(log_file, "w", encoding="utf-8") as f:
                f.write("\n".join(updated_lines) + "\n")

        # Save response
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")

        return "Submission successful!"
    return render_template('abstract_submit.html', model=model)


# === API to fetch next prompt ===
@app.route('/get_next/<model>')
def get_next(model):
    user_id = session.get("username", "anonymous")
    model = model.lower()
    if model not in MODELS:
        return jsonify({"error": "Model not supported."})
    return jsonify(get_next_prompt(model, user_id))


# === Run Locally ===
if __name__ == '__main__':
    app.run(debug=True)
