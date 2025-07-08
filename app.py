from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from datetime import datetime
import json, os, difflib, time
import jsonlines
from pathlib import Path
from collections import defaultdict
from apscheduler.schedulers.background import BackgroundScheduler
import random

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# === Constants ===
USERS_FILE = 'users.json'
PERSIST_DIR = '/var/data'
INPUT_FILE = os.path.join(PERSIST_DIR, 'inputs', 'arxiv_2000_2025_all_final.jsonl')
OUTPUT_DIR = os.path.join(PERSIST_DIR, 'outputs')
PROGRESS_DIR = os.path.join(PERSIST_DIR, 'progress')
USER_LOG_DIR = os.path.join(PERSIST_DIR, 'user_logs')
TIMEOUT_SECONDS = 1800
MODELS = ["gemini_flash", "grok", "chatgpt_4o_mini", "claude", "copilot"]

# === Ensure directories exist ===
for path in [os.path.dirname(INPUT_FILE), OUTPUT_DIR, PROGRESS_DIR, USER_LOG_DIR]:
    os.makedirs(path, exist_ok=True)

for model in MODELS:
    open(os.path.join(OUTPUT_DIR, f"output_{model}.jsonl"), 'a').close()

# === User File Setup ===
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'w') as f:
        json.dump({}, f)

with open(USERS_FILE, 'r+') as f:
    users = json.load(f)
    if 'admin' not in users:
        users['admin'] = {
            'password': generate_password_hash("testgptmodels"),
            'email': 'admin@example.com',
            'phone': '0000000000'
        }
        f.seek(0)
        json.dump(users, f, indent=2)
        f.truncate()

# === Routes ===
@app.route('/')
def home():
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm = request.form['confirm_password']
        email = request.form['email']
        phone = request.form['phone']

        if password != confirm:
            flash("Passwords do not match")
            return redirect(url_for('register'))

        with open(USERS_FILE, 'r+') as f:
            users = json.load(f)
            if username in users:
                flash("Username already exists")
                return redirect(url_for('register'))
            users[username] = {
                'password': generate_password_hash(password),
                'email': email,
                'phone': phone
            }
            f.seek(0)
            json.dump(users, f, indent=2)
            f.truncate()

        flash("Registration successful")
        return redirect(url_for('home'))

    return render_template('register.html')

@app.route('/login', methods=['POST'])
def login():
    username = request.form['username']
    password = request.form['password']
    with open(USERS_FILE) as f:
        users = json.load(f)
    if username in users and check_password_hash(users[username]['password'], password):
        session['username'] = username
        return redirect(url_for('admin_dashboard') if username == 'admin' else url_for('submit'))
    flash("Invalid credentials")
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/submit')
def submit():
    if 'username' not in session:
        return redirect(url_for('home'))
    return render_template('submit.html', username=session['username'])

@app.route('/get_next/<model>')
def get_next(model):
    if 'username' not in session:
        return jsonify({"error": "Not logged in"})
    username = session['username']
    return jsonify(get_next_prompt(model, username))

def get_next_prompt(model_name: str, user_id: str) -> dict:
    progress_file = os.path.join(PROGRESS_DIR, f"{user_id}_{model_name}.json")
    assigned_ids = set()

    if os.path.exists(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            assigned_ids = set(json.load(f).keys())

    with jsonlines.open(INPUT_FILE) as reader:
        for idx, obj in enumerate(reader):
            prompt_id = str(obj.get("id", idx))
            if prompt_id not in assigned_ids:
                return {
                    "id": prompt_id,
                    "title": obj.get("title", ""),
                    "prompt": obj,
                    "completed": len(assigned_ids)
                }

    return {"message": "✅ All prompts completed!"}

@app.route('/submit/<model>', methods=['POST'])
def submit_response(model):
    if 'username' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})

    data = request.get_json()
    username = session['username']
    response = data.get('response', '').strip()
    title = data.get('title', '').strip()
    prompt_id = str(data.get('id', '')).strip()

    if len(response.split()) < 50:
        return jsonify({'status': 'error', 'message': 'Response must be at least 50 words'})

    entry_id = str(uuid4())
    timestamp = datetime.utcnow().isoformat()

    entry = {
        'uuid': entry_id,
        'id': prompt_id,
        'title': title,
        'response': response,
        'model': model,
        'username': username,
        'word_count': len(response.split()),
        'sentence_count': response.count('.') + response.count('!') + response.count('?'),
        'character_count': len(response),
        'timestamp': timestamp
    }

    with open(os.path.join(OUTPUT_DIR, f"output_{model}.jsonl"), 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')

    with open(os.path.join(USER_LOG_DIR, f"{username}_{model}.jsonl"), 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')

    progress_file = os.path.join(PROGRESS_DIR, f"{username}_{model}.json")
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
    progress[prompt_id] = entry_id
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

    return jsonify({'status': 'success', 'uuid': entry_id})

@app.route('/admin_dashboard')
def admin_dashboard():
    if 'username' not in session or session['username'] != 'admin':
        return redirect(url_for('home'))

    model_map = {m: m.replace('_', ' ').title() for m in MODELS}
    usernames = set()
    total_counts = {m: 0 for m in MODELS}
    user_model_map = defaultdict(lambda: defaultdict(int))
    daily_user_raw = defaultdict(lambda: defaultdict(int))
    top_contributors = []

    for model in MODELS:
        path = os.path.join(OUTPUT_DIR, f'output_{model}.jsonl')
        if not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    username = data.get("username")
                    date_str = data.get("timestamp", "").split("T")[0]
                    if not username:
                        continue
                    usernames.add(username)
                    total_counts[model] += 1
                    user_model_map[username][model] += 1
                    daily_user_raw[date_str][username] += 1
                except:
                    continue

    total_answers = {
        "labels": [model_map[m] for m in MODELS],
        "counts": [total_counts[m] for m in MODELS]
    }

    user_model_activity = {
        "models": [model_map[m] for m in MODELS],
        "users": [
            {
                "username": user,
                "counts": [user_model_map[user].get(m, 0) for m in MODELS],
                "color": f"#{random.randint(0, 0xFFFFFF):06x}"
            }
            for user in sorted(usernames)
        ]
    }

    sorted_dates = sorted(daily_user_raw.keys())
    daily_user_activity_chart = {
        "dates": sorted_dates,
        "users": [
            {
                "username": user,
                "counts": [daily_user_raw[date].get(user, 0) for date in sorted_dates],
                "color": f"#{random.randint(0, 0xFFFFFF):06x}"
            }
            for user in sorted(usernames)
        ]
    }

    for user in sorted(usernames):
        entry = {"username": user, "total": 0}
        for model in MODELS:
            count = user_model_map[user][model]
            entry[model] = count
            entry["total"] += count
        top_contributors.append(entry)

    top_contributors.sort(key=lambda x: x["total"], reverse=True)

    return render_template("admin_dashboard.html",
        total_answers=total_answers,
        user_model_activity=user_model_activity,
        daily_user_activity=daily_user_activity_chart,
        top_contributors=top_contributors
    )

@app.route('/user_dashboard')
def user_dashboard():
    if 'username' not in session:
        return redirect(url_for('home'))

    username = session['username']
    user_responses = []

    for model in MODELS:
        path = os.path.join(OUTPUT_DIR, f'output_{model}.jsonl')
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    data = json.loads(line)
                    if data.get('username') == username:
                        user_responses.append((model, data))

    model_counter = {m: 0 for m in MODELS}
    for model, _ in user_responses:
        model_counter[model] += 1

    model_counts = [{'name': m.replace('_', ' ').title(), 'count': model_counter[m]} for m in MODELS]

    return render_template('user_dashboard.html', username=username, model_counts=model_counts)

# === Flask App Start ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
