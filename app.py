from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from uuid import uuid4
from datetime import datetime
import os, json, difflib

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
db = SQLAlchemy(app)

USERS_FILE = 'users.json'
INPUT_FILE = 'backend/inputs/input.jsonl'
RESPONSES_DIR = 'responses'
OUTPUT_DIR = 'backend/outputs'
MODELS = ["gemini_flash", "grok", "chatgpt_4o_mini", "claude", "copilot"]

# Ensure directories and files exist
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
                    f'Prompt Template: Generate an academic abstract of 150 to 300 words on the topic "{entry["title"]}". '
                    f'Use a formal academic tone emphasizing clarity, objectivity, and technical accuracy. '
                    f'Response must follow this JSON format:\n'
                    f'{{"model name": "<model>", "Core_Model": "<core model>", "Title": "{entry["title"]}", '
                    f'"Abstract": "<your abstract>", "Keywords": "<comma-separated>"}}'
                )
                return jsonify({
                    'uuid': str(uuid4()),
                    'id': entry['id'],
                    'title': entry['title'],
                    'prompt': prompt
                })

    return jsonify({'prompt': None})

def show_diff(base, edited, threshold=0.85):
    if difflib.SequenceMatcher(None, base, edited).ratio() > threshold:
        return True
    return False

@app.route('/submit/<model>', methods=['POST'])
def submit_response(model):
    if 'username' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})

    try:
        data = request.json
        response = data['response'].strip()
        if len(response.split()) < 50:
            return jsonify({'status': 'error', 'message': 'Response too short'})

        for m in MODELS:
            filepath = os.path.join(RESPONSES_DIR, f"{m}.jsonl")
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    for line in f:
                        old = json.loads(line)
                        if show_diff(old.get('response', ''), response):
                            return jsonify({'status': 'duplicate', 'message': 'Duplicate response found'})

        entry = {
            'uuid': data['uuid'],
            'id': data['id'],
            'title': data['title'],
            'response': response,
            'model': model,
            'username': session['username'],
            'word_count': len(response.split()),
            'sentence_count': response.count('.') + response.count('!') + response.count('?'),
            'character_count': len(response),
            'timestamp': datetime.utcnow().isoformat()
        }

        with open(os.path.join(RESPONSES_DIR, f"{model}.jsonl"), 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

        return jsonify({'status': 'success'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/admin')
def admin_dashboard():
    if 'username' not in session or session['username'] != 'admin':
        return redirect(url_for('home'))
    return render_template('admin_dashboard.html')

@app.route('/user_dashboard')
def user_dashboard():
    if 'username' not in session:
        return redirect(url_for('home'))
    return render_template('user_dashboard.html', username=session['username'])

@app.route('/download/<model>')
def download_model(model):
    path = os.path.join(RESPONSES_DIR, f"{model}.jsonl")
    return send_from_directory(RESPONSES_DIR, f"{model}.jsonl", as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
