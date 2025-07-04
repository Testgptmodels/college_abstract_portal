# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from datetime import datetime, timedelta
import jsonlines, os, json, re
from collections import defaultdict, Counter
from flask import send_from_directory
import difflib

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
db = SQLAlchemy(app)

USERS_FILE = 'users.json'
RESPONSES_DIR = 'responses'
INPUT_FILE = 'backend/inputs/input.jsonl'
MODELS = ["gemini_flash", "grok", "chatgpt_4o_mini", "claude", "copilot"]

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

if not os.path.exists(RESPONSES_DIR):
    os.makedirs(RESPONSES_DIR)
    for model in MODELS:
        with open(os.path.join(RESPONSES_DIR, f"{model}.jsonl"), 'w') as f:
            pass

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
        if username == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('submit'))
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
    return render_template('abstract_submit.html', username=session['username'])

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
                except:
                    continue

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            entry = json.loads(line)
            if entry['id'] not in completed_ids:
                prompt = (
                    f'Prompt Template: Generate a academic abstract of 150 to 300 words on the topic "{entry["title"]}". '
                    f'Use a formal academic tone emphasizing clarity, objectivity, and technical accuracy. '
                    f'Avoid suggestions, conversational language, and introductory framing. The response should contain all the field '
                    f'/{{model name :"<model name>"  Core_Model: "<core model name>" Title: "<title content>" '
                    f'Abstract: "<abstract content>" Keywords: "<comma-separated keywords>"}} use valid json format.'
                )
                return jsonify({
                    'uuid': str(uuid.uuid4()),
                    'id': entry['id'],
                    'title': entry['title'],
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
            "diff": {
                "added": added,
                "removed": removed,
                "unchanged": unchanged[:10]
            }
        }
    else:
        return {
            "status": "unique",
            "message": "Answer is unique."
        }

@app.route('/submit/<model>', methods=['POST'])
def submit_response(model):
    if 'username' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})

    data = request.json
    response = data['response'].strip()
    expected_title = data['title'].strip()

    if not response.startswith(expected_title):
        return jsonify({'status': 'error', 'message': 'First line must match the title exactly.'})

    if len(response.split()) < 50:
        return jsonify({'status': 'error', 'message': 'Response must be at least 50 words'})

    for m in MODELS:
        path = os.path.join(RESPONSES_DIR, f"{m}.jsonl")
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    entry = json.loads(line)
                    base = entry.get('response', '')
                    diff_result = show_diff(base, response)
                    if diff_result['status'] == 'duplicate':
                        return jsonify({
                            'status': 'duplicate',
                            'message': diff_result['message'],
                            'diff': diff_result['diff']
                        })

    word_count = len(response.split())
    sentence_count = response.count('.') + response.count('!') + response.count('?')
    char_count = len(response)

    entry = {
        'uuid': data['uuid'],
        'id': data['id'],
        'title': expected_title,
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

    return jsonify({'status': 'success'})


# (Other routes are unchanged, but similar modifications can be made to integrate Copilot and new prompt templates.)


@app.route('/user_dashboard')
def user_dashboard():
    if 'username' not in session:
        return redirect(url_for('home'))
    model_counts = []
    for model in MODELS:
        count = 0
        path = os.path.join(RESPONSES_DIR, f"{model}.jsonl")
        with open(path) as f:
            for line in f:
                if json.loads(line).get("username") == session['username']:
                    count += 1
        model_counts.append({"name": model.replace('_', ' ').title(), "count": count})
    return render_template("user_dashboard.html", model_counts=model_counts, username=session['username'])

@app.route('/admin')
def admin_dashboard():
    if 'username' not in session or session['username'] != 'admin':
        return redirect(url_for('home'))

    total_answers = {"labels": [], "counts": []}
    for model in MODELS:
        total_answers["labels"].append(model.replace("_", " ").title())
        with open(os.path.join(RESPONSES_DIR, f"{model}.jsonl")) as f:
            total_answers["counts"].append(sum(1 for _ in f))

    user_model_activity = {"models": [m.replace('_', ' ').title() for m in MODELS], "users": []}
    user_counts = defaultdict(lambda: [0]*len(MODELS))
    color_cycle = ['#FF5733', '#33CFFF', '#7D3C98', '#2ECC71', '#FF33A1']

    for idx, model in enumerate(MODELS):
        with open(os.path.join(RESPONSES_DIR, f"{model}.jsonl")) as f:
            for line in f:
                obj = json.loads(line)
                user = obj["username"]
                user_counts[user][idx] += 1

    for i, (user, counts) in enumerate(user_counts.items()):
        user_model_activity["users"].append({
            "username": user,
            "counts": counts,
            "color": color_cycle[i % len(color_cycle)]
        })

    today = datetime.utcnow().date()
    daily_user_activity = {"dates": [], "users": []}
    user_daily = defaultdict(lambda: [0]*30)
    for idx, model in enumerate(MODELS):
        with open(os.path.join(RESPONSES_DIR, f"{model}.jsonl")) as f:
            for line in f:
                data = json.loads(line)
                date = datetime.fromisoformat(data['timestamp']).date()
                if (today - date).days < 30:
                    user_daily[data['username']][29 - (today - date).days] += 1
    for i, (user, counts) in enumerate(user_daily.items()):
        daily_user_activity["users"].append({
            "username": user,
            "counts": counts,
            "color": color_cycle[i % len(color_cycle)]
        })
    daily_user_activity['dates'] = [(today - timedelta(days=i)).isoformat() for i in reversed(range(30))]

    contributor_data = []
    for user, counts in user_counts.items():
        total = sum(counts)
        contributor_data.append({
            'username': user,
            'gemini_flash': counts[0],
            'grok': counts[1],
            'chatgpt_4o_mini': counts[2],
            'claude': counts[3],
            'copilot': counts[4],
            'total': total
        })
    contributor_data = sorted(contributor_data, key=lambda x: x['total'], reverse=True)

    return render_template("admin_dashboard.html", total_answers=total_answers, user_model_activity=user_model_activity,
                           daily_user_activity=daily_user_activity, top_contributors=contributor_data)

@app.route('/download/<model>')
def download_model(model):
    return send_from_directory(RESPONSES_DIR, f"{model}.jsonl", as_attachment=True)


@app.route('/receipt/<username>')
def receipt(username):
    # Static admin details
    admin_info = {
        "name": "Ambrish G",
        "email": "testgptmodels@gmail.com",
        "phone": "0000000000"
    }

    # Load user info from users.json
    with open(USERS_FILE) as f:
        users = json.load(f)
    user_info = users.get(username)
    if not user_info:
        flash(f"No such user: {username}")
        return redirect(url_for('admin_dashboard'))

    # Count abstracts per model
    counts = []
    items = []
    price_per_abstract = 0.1  # You can change this to any value
    for model in MODELS:
        count = 0
        path = os.path.join(RESPONSES_DIR, f"{model}.jsonl")
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if json.loads(line).get("username") == username:
                        count += 1
        if count > 0:
            items.append({
                "description": f"{model.replace('_', ' ').title()} abstracts",
                "quantity": count,
                "price": price_per_abstract,
                "amount": count * price_per_abstract
            })
        counts.append(count)

    total = sum(item["amount"] for item in items)

    context = dict(
        receipt_number=f"RCPT-{uuid4().hex[:8].upper()}",
        receipt_date=datetime.utcnow().strftime("%Y-%m-%d"),
        from_name=admin_info["name"],
        from_email=admin_info["email"],
        from_phone=admin_info["phone"],
        to_name=username,
        to_email=user_info.get("email", ""),
        to_phone=user_info.get("phone", ""),
        items=items,
        amount=total,
        additional_charges=0,
        total=total
    )

    return render_template("receipt.html", **context)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)







