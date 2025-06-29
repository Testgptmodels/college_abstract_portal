from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from datetime import datetime, timedelta
import os, json, re
from collections import defaultdict, Counter
from flask import send_from_directory

app = Flask(__name__)
app.secret_key = 'supersecretkey'

USERS_FILE = 'users.json'
RESPONSES_DIR = 'responses'
INPUT_FILE = 'backend/inputs/input.jsonl'
MODELS = ["gemini_flash", "grok", "chatgpt_4o_mini", "claude"]

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
    if not os.path.exists(INPUT_FILE):
        return jsonify({'title': None})

    with open(INPUT_FILE) as f:
        titles = [json.loads(line.strip()) for line in f if line.strip()]

    submitted = set()
    user_responses = get_all_user_responses(session['username'])
    for r in user_responses:
        submitted.add((r['model'], r['id']))

    for i, entry in enumerate(titles):
        if (model, i) not in submitted:
            return jsonify({
                'uuid': str(uuid4()),
                'id': i,
                'title': f"Generate an academic abstract for the paper titled with minimum 150 to 300 words \"{entry['title']}\""
            })

    return jsonify({'title': None})

def get_all_user_responses(username):
    all_data = []
    for model in MODELS:
        filepath = os.path.join(RESPONSES_DIR, f"{model}.jsonl")
        if os.path.exists(filepath):
            with open(filepath) as f:
                for line in f:
                    data = json.loads(line)
                    if data.get('username') == username:
                        all_data.append(data)
    return all_data

def count_text_stats(text):
    word_count = len(text.split())
    sentence_count = text.count('.') + text.count('!') + text.count('?')
    char_count = len(text)
    return word_count, sentence_count, char_count


@app.route('/submit/<model>', methods=['POST'])
def submit(model):  # changed from `submit` to `submit_abstract`
    if 'username' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})

    data = request.json
    response = data['response'].strip()
    if len(response.split()) < 50:
        return jsonify({'status': 'error', 'message': 'Response must be at least 50 words'})

    # Check duplicate across all models
    for m in MODELS:
        path = os.path.join(RESPONSES_DIR, f"{m}.jsonl")
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    entry = json.loads(line)
                    if entry.get('response') == response:
                        return jsonify({'status': 'duplicate'})

    # Clean the title
    title = re.sub(
        r'^Generate an academic abstract for the paper titled with minimum 150 to 300 words \"',
        '', data['title']
    ).rstrip('"')

    # Get stats
    word_count, sentence_count, char_count = count_text_stats(response)

    # Save response
    entry = {
        'uuid': data['uuid'],
        'id': data['id'],
        'title': title,
        'response': response,
        'word_count': word_count,
        'sentence_count': sentence_count,
        'character_count': char_count,
        'username': session['username'],
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

    output_path = os.path.join('backend/outputs', f'output_{model}.jsonl')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')

    return jsonify({'status': 'success'})



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
    counts = []
    for model in MODELS:
        count = 0
        with open(os.path.join(RESPONSES_DIR, f"{model}.jsonl")) as f:
            for line in f:
                if json.loads(line).get("username") == username:
                    count += 1
        counts.append(count)
    return render_template("receipt.html", username=username, counts=counts, models=MODELS)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
