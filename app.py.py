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
from datetime import datetime, timedelta



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

    # Track submitted and recently assigned prompt IDs
    if os.path.exists(user_log_file):
        with open(user_log_file, 'r') as f:
            for line in f:
                entry = json.loads(line)
                prompt_id = str(entry["id"])
                if entry.get("submitted"):
                    assigned_ids.add(prompt_id)  # Already submitted
                elif now - entry.get("assigned_at", 0) < TIMEOUT_SECONDS:
                    assigned_ids.add(prompt_id)  # Still within timeout
                    active_assignments[prompt_id] = entry

    completed = len([
        1 for v in active_assignments.values()
        if v.get("submitted")
    ])

    # Find the next unassigned prompt
    with jsonl_open(INPUT_FILE) as reader:
        for idx, obj in enumerate(reader):
            prompt_id = str(obj.get("id") or idx)
            if prompt_id not in assigned_ids:
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

                obj["id"] = prompt_id
                obj["prompt"] = (
                    f'Prompt Template: Generate a academic abstract of 150 to 300 words on the topic "{obj["title"]}". '
                    'Use a formal academic tone emphasizing clarity, objectivity, and technical accuracy. '
                    'Avoid suggestions, conversational language, and introductory framing. The response should contain all the below mention '
                    '{"model name":"<GPT model name - the name of the AI model generating the response>", '
                    '"Core_Model":"<core GPT model name - name of the core language model used>", '
                    '"Title":"<title content>", '
                    '"Abstract":"<abstract content - should match the title!>", '
                    '"Keywords":"<comma-separated keywords - should match the domain of the abstract>", '
                    '"think":"should reflect reasoning behind abstract generation", '
                    '"word_count": word count of abstract, '
                    '"sentence_count": Sentence count of abstract, '
                    '"character_count": character count of abstract, '
                    '"generated_at":"Timestamp"} '
                    'Use valid JSON format.'
                )
                return obj

    return {"title": None, "prompt": None}



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


# Define the helper function at global scope
def compute_top_contributors():
    user_data = {}

    for model in MODELS:
        output_file = os.path.join(OUTPUT_DIR, f"output_{model}.jsonl")
        if not os.path.exists(output_file):
            continue
        with open(output_file, "r", encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                username = entry.get("username", "unknown")
                if username not in user_data:
                    user_data[username] = {m: 0 for m in MODELS}
                    user_data[username]["total"] = 0
                user_data[username][model] += 1
                user_data[username]["total"] += 1

    contributors = []
    for user, counts in user_data.items():
        contributors.append({
            "username": user,
            "total": counts["total"],
            "gemini_flash": counts["gemini_flash"],
            "grok": counts["grok"],
            "chatgpt_4o_mini": counts["chatgpt_4o_mini"],
            "claude": counts["claude"],
            "copilot": counts["copilot"]
        })

    contributors.sort(key=lambda x: x["total"], reverse=True)
    return contributors


from collections import defaultdict
from datetime import datetime, timedelta

@app.route("/admin_dashboard")
def admin_dashboard():
    if "username" not in session or session["username"] != "admin":
        return redirect(url_for("login"))

    def compute_top_contributors():
        user_data = {}
        for model in MODELS:
            output_file = os.path.join(OUTPUT_DIR, f"output_{model}.jsonl")
            if not os.path.exists(output_file):
                continue
            with open(output_file, "r", encoding="utf-8") as f:
                for line in f:
                    entry = json.loads(line)
                    username = entry.get("username", "unknown")
                    if username not in user_data:
                        user_data[username] = {m: 0 for m in MODELS}
                        user_data[username]["total"] = 0
                    user_data[username][model] += 1
                    user_data[username]["total"] += 1

        contributors = []
        for user, counts in user_data.items():
            contributors.append({
                "username": user,
                "total": counts["total"],
                "gemini_flash": counts["gemini_flash"],
                "grok": counts["grok"],
                "chatgpt_4o_mini": counts["chatgpt_4o_mini"],
                "claude": counts["claude"],
                "copilot": counts["copilot"]
            })

        contributors.sort(key=lambda x: x["total"], reverse=True)
        return contributors

    top_contributors = compute_top_contributors()

    total_answers = {
        "labels": ["Gemini Flash", "Grok", "ChatGPT 4o Mini", "Claude", "Microsoft Copilot"],
        "counts": [
            sum(1 for _ in open(os.path.join(OUTPUT_DIR, f"output_{model}.jsonl"), encoding="utf-8")) if os.path.exists(os.path.join(OUTPUT_DIR, f"output_{model}.jsonl")) else 0
            for model in MODELS
        ]
    }

    user_model_activity = {
        "models": MODELS,
        "users": [
            {"username": user["username"], "counts": [
                user["gemini_flash"], user["grok"], user["chatgpt_4o_mini"],
                user["claude"], user["copilot"]
            ], "color": f"hsl({(i*65)%360},70%,50%)"}
            for i, user in enumerate(top_contributors[:6])
        ]
    }

    # ✅ Generate REAL Daily User Activity (last 15 days)
    date_format = "%Y-%m-%d"
    today = datetime.now().date()
    dates = [(today - timedelta(days=i)).strftime(date_format) for i in reversed(range(15))]

    user_counts_by_day = defaultdict(lambda: defaultdict(int))
    for model in MODELS:
        output_file = os.path.join(OUTPUT_DIR, f"output_{model}.jsonl")
        if os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8") as f:
                for line in f:
                    entry = json.loads(line)
                    username = entry.get("username", "unknown")
                    timestamp = entry.get("timestamp")
                    if timestamp:
                        date_str = timestamp.split("T")[0]
                        if date_str in dates:
                            user_counts_by_day[username][date_str] += 1

    daily_user_activity = {
        "dates": dates,
        "users": [
            {
                "username": username,
                "counts": [user_counts_by_day[username][d] for d in dates],
                "color": f"hsl({(i*45)%360}, 60%, 50%)"
            }
            for i, username in enumerate(list(user_counts_by_day.keys())[:4])
        ]
    }

    return render_template("admin_dashboard.html",
        top_contributors=top_contributors,
        total_answers=total_answers,
        user_model_activity=user_model_activity,
        daily_user_activity=daily_user_activity
    )


@app.route("/receipt/<username>")
def receipt(username):
    base_price_per_submission = 0.10
    additional_charges = 0.0

    # Load user details from users.json
    with open(USERS_FILE, 'r') as f:
        users = json.load(f)
    user_info = users.get(username, {})
    to_phone = user_info.get("phone", "N/A")
    to_email = user_info.get("email", f"{username}@gmail.com")

    # Collect submission data
    items = []
    total_submitted = 0

    for model in MODELS:
        file_path = os.path.join(OUTPUT_DIR, f"output_{model}.jsonl")
        if not os.path.exists(file_path):
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            count = 0
            for line in f:
                entry = json.loads(line)
                if entry.get("username") == username:
                    count += 1
            if count > 0:
                items.append({
                    "description": f"{model.replace('_', ' ').title()} Abstracts",
                    "quantity": count,
                    "price": base_price_per_submission,
                    "amount": base_price_per_submission * count
                })
                total_submitted += count

    amount = sum(item["amount"] for item in items)
    total = amount + additional_charges

    return render_template("receipt.html",
        receipt_number=f"R-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        receipt_date=datetime.now().strftime("%Y-%m-%d"),
        from_name="Admin",
        from_phone="8660946035",
        from_email="testgptmodels@gmail.com",
        to_name=username,
        to_phone=to_phone,
        to_email=to_email,
        items=items,
        amount=amount,
        additional_charges=additional_charges,
        total=total
    )

from flask import send_file

@app.route('/download/<model>')
def download_model(model):
    filepath = os.path.join(OUTPUT_DIR, f"output_{model}.jsonl")
    if not os.path.exists(filepath):
        return f"No output found for model: {model}", 404
    return send_file(filepath, as_attachment=True)



@app.route("/downloads")
def list_downloads():
    if "username" not in session or session["username"] != "admin":
        return abort(403)

    allowed_dirs = ["outputs", "responses", "user_logs", "progress", "inputs"]
    files = []

    for folder in allowed_dirs:
        dir_path = os.path.join("/var/data", folder)
        if os.path.isdir(dir_path):
            for file in os.listdir(dir_path):
                files.append({"folder": folder, "name": file})

    return render_template("download_list.html", files=files)

