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
from collections import defaultdict
from jsonlines import open as jsonl_open

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# === Constants ===
USERS_FILE = 'users.json'
PERSIST_DIR = '/var/data'
INPUT_FILE = os.path.join(PERSIST_DIR, 'inputs', 'arxiv_2000_2025_all_final.jsonl')
RESPONSES_DIR = os.path.join(PERSIST_DIR, 'responses')
OUTPUT_DIR = os.path.join(PERSIST_DIR, 'outputs')
PROGRESS_DIR = os.path.join(PERSIST_DIR, 'progress')
USER_LOG_DIR = os.path.join(PERSIST_DIR, 'user_logs')
TIMEOUT_SECONDS = 900  # 15 minutes
MODELS = ["gemini_flash", "grok", "chatgpt_4o_mini", "claude", "copilot"]

# === Ensure directories ===
for path in [RESPONSES_DIR, OUTPUT_DIR, PROGRESS_DIR, USER_LOG_DIR, os.path.dirname(INPUT_FILE)]:
    os.makedirs(path, exist_ok=True)

# === Load Users ===
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

# === Prompt Allocation with Expiry Check ===
def get_next_prompt(model, username):
    user_log_file = os.path.join(USER_LOG_DIR, f"{model}_users.jsonl")
    assigned_ids = set()
    active_assignments = {}
    now = int(time.time())

    if os.path.exists(user_log_file):
        with open(user_log_file, 'r') as f:
            for line in f:
                entry = json.loads(line)
                if not entry.get("submitted"):
                    assigned_at = entry.get("assigned_at", 0)
                    if now - assigned_at < TIMEOUT_SECONDS:
                        assigned_ids.add(entry["id"])
                        active_assignments[entry["id"]] = entry

    with jsonl_open(INPUT_FILE) as reader:
        for idx, obj in enumerate(reader):
            prompt_id = str(obj.get("id") or idx)
            if prompt_id not in assigned_ids:
                title = obj.get("title", "Untitled")
                timestamp = int(time.time())
                log_entry = {
                    "username": username,
                    "model": model,
                    "id": prompt_id,
                    "assigned_at": timestamp,
                    "submitted": False
                }
                with open(user_log_file, 'a') as log:
                    log.write(json.dumps(log_entry) + "\n")

                prompt_text = (
                    f'Prompt Template: Generate a academic abstract of 150 to 300 words on the topic "{title}". '
                    f'Use a formal academic tone emphasizing clarity, objectivity, and technical accuracy. Avoid suggestions, '
                    f'conversational language, and introductory framing. The response should contain all the below mention:\n\n'
                    '{\n'
                    '  "model name": "<GPT model name - the name of the AI model generating the response>",\n'
                    '  "Core_Model": "<core GPT model name -  name of the core language model used >",\n'
                    f'  "Title": "{title}",\n'
                    '  "Abstract": "<abstract content - should match the title!>",\n'
                    '  "Keywords": "<comma-separated keywords - should match the domain of the abstract>",\n'
                    '  "think": "<should reflect reasoning behind abstract generation>",\n'
                    '  "word_count": <word count of abstract>,\n'
                    '  "sentence_count": <sentence count of abstract>,\n'
                    '  "character_count": <character count of abstract>,\n'
                    '  "generated_at": "<Timestamp>"\n'
                    '}'
                )

                return {
                    "prompt": prompt_text,
                    "id": prompt_id,
                    "uuid": str(obj.get("uuid", str(uuid4()))),
                    "title": title,
                    "completed": len(assigned_ids)
                }


@app.route('/get_next/<model>')
def get_next(model):
    if 'username' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(get_next_prompt(model, session['username']))

# === Reassignment Logic ===
def reassign_expired_prompts():
    now = int(time.time())
    for model in MODELS:
        user_log_file = os.path.join(USER_LOG_DIR, f"{model}_users.jsonl")
        if not os.path.exists(user_log_file):
            continue
        valid_entries = []
        with open(user_log_file, 'r') as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("submitted") or now - entry.get("assigned_at", 0) <= TIMEOUT_SECONDS:
                    valid_entries.append(entry)
        with open(user_log_file, 'w') as f:
            for entry in valid_entries:
                f.write(json.dumps(entry) + '\n')

scheduler = BackgroundScheduler()
scheduler.add_job(reassign_expired_prompts, 'interval', minutes=5)
scheduler.start()

# === Submit Route ===
@app.route('/submit/<model>', methods=['POST'])
def submit_response(model):
    if 'username' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})

    data = request.get_json()
    username = session['username']
    response = data.get('response', '').strip()
    title = data.get('title', '').strip()
    prompt_id = str(data.get('id'))

    if len(response.split()) < 50:
        return jsonify({'status': 'error', 'message': 'Response must be at least 50 words'})

    word_count = len(response.split())
    sentence_count = response.count('.') + response.count('!') + response.count('?')
    char_count = len(response)

    entry = {
        'uuid': str(uuid4()),
        'id': prompt_id,
        'title': title,
        'response': response,
        'model': model,
        'username': username,
        'word_count': word_count,
        'sentence_count': sentence_count,
        'character_count': char_count,
        'timestamp': datetime.utcnow().isoformat()
    }

    with open(os.path.join(RESPONSES_DIR, f"{model}.jsonl"), 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')
    with open(os.path.join(OUTPUT_DIR, f"output_{model}.jsonl"), 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')

    user_log_file = os.path.join(USER_LOG_DIR, f"{model}_users.jsonl")
    updated = []
    with open(user_log_file, 'r') as f:
        for line in f:
            e = json.loads(line)
            if e['id'] == prompt_id and e['username'] == username:
                e['submitted'] = True
            updated.append(e)
    with open(user_log_file, 'w') as f:
        for e in updated:
            f.write(json.dumps(e) + '\n')

    return jsonify({'status': 'success'})


# === Add remaining routes (register, login, dashboard, etc) below ===
@app.route("/")
def home():
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
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

@app.route("/login", methods=["POST"])
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

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))
@app.route("/submit")


def submit():
    if 'username' not in session:
        return redirect(url_for('home'))
    return render_template("submit.html", username=session['username'])

@app.route("/user_dashboard")
def user_dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

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

@app.route("/admin_dashboard")
def admin_dashboard():
    if 'username' not in session or session['username'] != 'admin':
        return redirect(url_for('login'))

    total_answers = {"labels": [], "counts": []}
    user_model_activity = {"models": [], "users": []}
    daily_user_raw = defaultdict(lambda: defaultdict(int))
    top_contributors = []
    usernames = set()

    model_map = dict(zip(MODELS, [m.replace("_", " ").title() for m in MODELS]))
    total_counts = {m: 0 for m in MODELS}
    user_model_map = defaultdict(lambda: defaultdict(int))

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
                except: continue

    total_answers["labels"] = [model_map[m] for m in MODELS]
    total_answers["counts"] = [total_counts[m] for m in MODELS]

    def get_color(): return f"#{random.randint(0, 0xFFFFFF):06x}"

    for user in sorted(usernames):
        entry = {
            "username": user,
            "counts": [user_model_map[user].get(m, 0) for m in MODELS],
            "color": get_color()
        }
        user_model_activity["users"].append(entry)
    user_model_activity["models"] = [model_map[m] for m in MODELS]

    sorted_dates = sorted(daily_user_raw.keys())
    daily_user_activity_chart = {"dates": sorted_dates, "users": []}

    for user in sorted(usernames):
        counts = [daily_user_raw[date].get(user, 0) for date in sorted_dates]
        daily_user_activity_chart["users"].append({
            "username": user,
            "counts": counts,
            "color": get_color()
        })

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


