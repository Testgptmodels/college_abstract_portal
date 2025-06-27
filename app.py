# backend/app.py

from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import jsonlines, os
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
db = SQLAlchemy(app)

# --- MODELS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class SessionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    login_time = db.Column(db.DateTime, default=datetime.utcnow)
    logout_time = db.Column(db.DateTime)

# --- HELPERS ---
def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with jsonlines.open(path) as reader:
        return list(reader)

def append_jsonl(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with jsonlines.open(path, mode='a') as writer:
        writer.write(data)

def get_next_abstract(input_path, output_path):
    inputs = load_jsonl(input_path)
    outputs = load_jsonl(output_path)
    answered_ids = {entry['id'] for entry in outputs}
    for entry in inputs:
        if entry['id'] not in answered_ids:
            return entry
    return None

def count_text_stats(text):
    words = text.split()
    sentences = text.count('.') + text.count('!') + text.count('?')
    return len(words), sentences, len(text)

# --- ROUTES ---
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        db.session.add(User(username=username, password=password))
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            db.session.add(SessionLog(user_id=user.id))
            db.session.commit()
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session.get('is_admin'):
        # Admin dashboard
        stats = {}
        models = ['gemini_flash', 'grok', 'claude']
        for model in models:
            path = f'backend/outputs/output_{model}.jsonl'
            stats[model] = len(load_jsonl(path)) if os.path.exists(path) else 0
        return render_template('dashboard.html', stats=stats)
    return render_template('abstract_submit.html', username=session['username'])

@app.route('/get_next/<model>')
def get_next(model):
    input_path = 'backend/inputs/input.jsonl'
    output_path = f'backend/outputs/output_{model}.jsonl'
    abstract = get_next_abstract(input_path, output_path)
    return jsonify(abstract or {})

@app.route('/submit/<model>', methods=['POST'])
def submit(model):
    data = request.json
    response = data['response']
    username = session['username']
    word_count, sentence_count, char_count = count_text_stats(response)
    entry = {
        'uuid': data['uuid'],
        'id': data['id'],
        'title': data['title'],
        'username': username,
        'response': response,
        'word_count': word_count,
        'sentence_count': sentence_count,
        'character_count': char_count,
        'timestamp': datetime.now().isoformat()
    }
    output_path = f'backend/outputs/output_{model}.jsonl'
    append_jsonl(output_path, entry)
    return jsonify({'status': 'success'})

@app.route('/logout')
def logout():
    if 'user_id' in session:
        last_session = SessionLog.query.filter_by(user_id=session['user_id']).order_by(SessionLog.id.desc()).first()
        if last_session:
            last_session.logout_time = datetime.utcnow()
            db.session.commit()
    session.clear()
    return redirect(url_for('home'))

@app.route('/healthz')
def healthz():
    return 'ok', 200

# --- RUN ---
if __name__ == '__main__':
    os.makedirs('backend/outputs', exist_ok=True)
    os.makedirs('backend/inputs', exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True)
