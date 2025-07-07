from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from datetime import datetime
import json, os, difflib
import random
from flask import render_template

app = Flask(__name__)
app.secret_key = 'supersecretkey'

USERS_FILE = 'users.json'
RESPONSES_DIR = 'responses'
INPUT_FILE = 'backend/inputs/input.jsonl'
OUTPUT_DIR = 'backend/outputs'
MODELS = ["gemini_flash", "grok", "chatgpt_4o_mini", "claude", "copilot"]

os.makedirs(RESPONSES_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

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

for model in MODELS:
    open(os.path.join(RESPONSES_DIR, f"{model}.jsonl"), 'a').close()
    open(os.path.join(OUTPUT_DIR, f"output_{model}.jsonl"), 'a').close()

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
    completed_ids = set()
    output_file = os.path.join(OUTPUT_DIR, f'output_{model}.jsonl')
    
    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    completed_ids.add(data['id'])
                except Exception:
                    continue

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            entry = json.loads(line)
            if entry['id'] not in completed_ids:
                prompt = (
                    f'Prompt Template: Generate a academic abstract of 150 to 300 words on the topic "{entry["title"]}". '
                    f'Use a formal academic tone emphasizing clarity, objectivity, and technical accuracy. '
                    f'Avoid suggestions, conversational language, and introductory framing. The response should contain all the below mention '
                    f'{{"model name":"<GPT model name - the name of the AI model generating the response>" , "Core_Model": "<core GPT model name -  name of the core language model used >", "Title":"<title content>", '
                    f'"Abstract":"<abstract content - should match the title!>", "Keywords":"<comma-separated keywords - should match the domain of the abstract","think":"should reflect reasoning behind abstract generation","word_count":word count of abstract, "sentence_count": Sentence count of abstract, "character_count":character count of abstract, "generated_at":"Timestamp"}} use valid json format.'
                )
                return jsonify({
                    'uuid': str(uuid4()),
                    'id': entry['id'],
                    'title': prompt,
                    'prompt': prompt
                })

    return jsonify({'prompt': None})

def show_diff(base, edited, similarity_threshold=0.85):
    base_words = base.split()
    edited_words = edited.split()
    diff = difflib.ndiff(base_words, edited_words)

    added, removed, unchanged = [], [], []
    for line in diff:
        if line.startswith('+ '): added.append(line[2:])
        elif line.startswith('- '): removed.append(line[2:])
        elif line.startswith('  '): unchanged.append(line[2:])

    ratio = difflib.SequenceMatcher(None, base, edited).ratio()

    if ratio > similarity_threshold:
        return {
            "status": "duplicate",
            "message": "Duplicate or similar response.",
            "diff": {"added": added, "removed": removed, "unchanged": unchanged[:10]}
        }
    return {"status": "unique", "message": "Answer is unique."}

@app.route('/submit/<model>', methods=['POST'])
def submit_response(model):
    if 'username' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})

    data = request.get_json()
    response = data.get('response', '').strip()

    if len(response.split()) < 50:
        return jsonify({'status': 'error', 'message': 'Response must be at least 50 words'})

    for m in MODELS:
        path = os.path.join(RESPONSES_DIR, f"{m}.jsonl")
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        base = entry.get('response', '')
                        if show_diff(base, response)['status'] == 'duplicate':
                            return jsonify({'status': 'duplicate', 'message': 'Duplicate answer detected'})
                    except:
                        continue

    word_count = len(response.split())
    sentence_count = response.count('.') + response.count('!') + response.count('?')
    char_count = len(response)

    entry = {
        'uuid': data['uuid'],
        'id': data['id'],
        'title': data['title'],
        'response': response,
        'model': model,
        'username': session['username'],
        'word_count': word_count,
        'sentence_count': sentence_count,
        'character_count': char_count,
        'timestamp': datetime.utcnow().isoformat()
    }

    with open(os.path.join(RESPONSES_DIR, f"{model}.jsonl"), 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')
    
    with open(os.path.join(OUTPUT_DIR, f"output_{model}.jsonl"), 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')

    return jsonify({'status': 'success'})

@app.route('/admin_dashboard')
def admin_dashboard():
    if 'username' not in session or session['username'] != 'admin':
        return redirect(url_for('login'))

    import os
    import json
    import random
    from collections import defaultdict

    # Setup
    total_answers = {"labels": [], "counts": []}
    user_model_activity = {"models": [], "users": []}
    daily_user_raw = defaultdict(lambda: defaultdict(int))  # date -> user -> count
    top_contributors = []
    usernames = set()

    model_names = [m.replace("_", " ").title() for m in MODELS]
    model_map = dict(zip(MODELS, model_names))
    total_counts = {m: 0 for m in MODELS}
    user_model_map = defaultdict(lambda: defaultdict(int))  # username -> model -> count

    # Read data from .jsonl files
    for model in MODELS:
        path = os.path.join(OUTPUT_DIR, f'output_{model}.jsonl')
        if not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    username = data.get("username")
                    date_str = data.get("generated_at", "").split("T")[0]
                    if not username:
                        continue
                    usernames.add(username)
                    total_counts[model] += 1
                    user_model_map[username][model] += 1
                    daily_user_raw[date_str][username] += 1
                except Exception as e:
                    print(f"Error reading line: {e}")

    # Chart 1: Total answers per model
    total_answers["labels"] = [model_map[m] for m in MODELS]
    total_answers["counts"] = [total_counts[m] for m in MODELS]

    # Chart 2: User activity per model
    def get_color():
        return f"#{random.randint(0, 0xFFFFFF):06x}"

    for user in sorted(usernames):
        entry = {
            "username": user,
            "counts": [user_model_map[user].get(m, 0) for m in MODELS],
            "color": get_color()
        }
        user_model_activity["users"].append(entry)
    user_model_activity["models"] = [model_map[m] for m in MODELS]

    # Chart 3: Daily user activity
    sorted_dates = sorted(daily_user_raw.keys())
    daily_user_activity_chart = {"dates": sorted_dates, "users": []}

    for user in sorted(usernames):
        counts = [daily_user_raw[date].get(user, 0) for date in sorted_dates]
        daily_user_activity_chart["users"].append({
            "username": user,
            "counts": counts,
            "color": get_color()
        })

    # Table: Top contributors
    for user in sorted(usernames):
        entry = {"username": user, "total": 0}
        for model in MODELS:
            count = user_model_map[user][model]
            entry[model] = count
            entry["total"] += count
        top_contributors.append(entry)

    top_contributors.sort(key=lambda x: x["total"], reverse=True)

    # Render template
    return render_template("admin_dashboard.html",
        total_answers=total_answers,
        user_model_activity=user_model_activity,
        daily_user_activity=daily_user_activity_chart,
        top_contributors=top_contributors
    )



@app.route('/user_dashboard')
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

    # Aggregate counts
    model_counter = {}
    for model in MODELS:
        model_counter[model] = 0
    for model, _ in user_responses:
        model_counter[model] += 1

    model_counts = [{'name': m.replace('_', ' ').title(), 'count': model_counter[m]} for m in MODELS]

    return render_template('user_dashboard.html', username=username, model_counts=model_counts)


@app.route('/download/<model>')
def download_model(model):
    filename = f'output_{model}.jsonl'
    output_dir = os.path.join('backend', 'outputs')
    filepath = os.path.join(output_dir, filename)
    if os.path.exists(filepath):
        return send_from_directory(output_dir, filename, as_attachment=True)
    else:
        flash(f"No output found for model: {model}", "warning")
        return redirect(url_for('admin_dashboard'))

@app.route('/receipt/<username>')
def receipt(username):
    from_name = "Project Admin"
    from_phone = "1234567890"
    from_email = "admin@example.com"

    to_name = username
    to_phone = "9876543210"
    to_email = f"{username}@example.com"

    items = []
    total = 0.0
    additional_charges = 0.0

    for model in MODELS:
        path = os.path.join(OUTPUT_DIR, f'output_{model}.jsonl')
        if not os.path.exists(path):
            continue

        count = 0
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                data = json.loads(line)
                if data.get("username") == username:
                    count += 1

        if count > 0:
            price = 0.10
            amount = count * price
            total += amount
            items.append({
                "description": model.replace("_", " ").title(),
                "quantity": count,
                "price": price,     # ✅ float, not string
                "amount": amount    # ✅ float, not string
            })

    final_total = total + additional_charges

    return render_template("receipt.html",
        receipt_number=f"R-{username[:3].upper()}-{random.randint(1000,9999)}",
        receipt_date=datetime.now().strftime("%Y-%m-%d"),
        from_name=from_name,
        from_phone=from_phone,
        from_email=from_email,
        to_name=to_name,
        to_phone=to_phone,
        to_email=to_email,
        items=items,
        amount=total,
        additional_charges=additional_charges,
        total=final_total
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # use the env var PORT or default to 5000
    app.run(host="0.0.0.0", port=port, debug=True)