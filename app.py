# backend/app.py

from flask import Flask, render_template, request, redirect, session, url_for, jsonify, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
import jsonlines, os, re
from werkzeug.security import generate_password_hash, check_password_hash
from collections import defaultdict, Counter

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
    login_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
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
@app.route('/', methods=['GET'])
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        if User.query.filter_by(username=username).first():
            flash("Username already exists. Try another.")
            return redirect(url_for('register'))
        password = generate_password_hash(request.form['password'])
        db.session.add(User(username=username, password=password))
        db.session.commit()

        with open('backend/users.txt', 'a') as f:
            f.write(f"{username}\n")

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
            if user.is_admin:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid username or password.")
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/dashboard', methods=['GET'])
def dashboard():
    if 'user_id' not in session or session.get('is_admin'):
        return redirect(url_for('login'))
    return render_template('abstract_submit.html', username=session['username'])

@app.route('/admin', methods=['GET'])
def admin_dashboard():
    if 'user_id' not in session or not session.get('is_admin'):
        return redirect(url_for('login'))

    models = ['gemini_flash', 'grok', 'claude']
    stats = {}
    user_model_count = defaultdict(lambda: Counter())
    daily_logins = defaultdict(int)

    for model in models:
        path = f'backend/outputs/output_{model}.jsonl'
        entries = load_jsonl(path)
        stats[model] = len(entries)
        for entry in entries:
            user_model_count[entry['username']][model] += 1

    for session_log in SessionLog.query.all():
        if session_log.login_time:
            date_str = session_log.login_time.date().isoformat()
            daily_logins[date_str] += 1

    contributor_count = Counter()
    for user, model_counts in user_model_count.items():
        contributor_count[user] = sum(model_counts.values())
    top_contributors = contributor_count.most_common(10)

    return render_template(
        'admin_dashboard.html',
        stats=stats,
        models=models,
        user_model_count=dict(user_model_count),
        daily_logins=dict(daily_logins),
        top_contributors=top_contributors
    )

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
    title = re.sub(r'^Generate an academic abstract for the paper titled with minimum 150 to 300 words ', '', data['title'])
    entry = {
        'uuid': data['uuid'],
        'id': data['id'],
        'title': title,
        'username': username,
        'response': response,
        'word_count': word_count,
        'sentence_count': sentence_count,
        'character_count': char_count,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    output_path = f'backend/outputs/output_{model}.jsonl'
    append_jsonl(output_path, entry)
    return jsonify({'status': 'success'})

@app.route('/download/<model>', methods=['GET'])
def download_output(model):
    if 'user_id' not in session or not session.get('is_admin'):
        return redirect(url_for('login'))
    path = f'backend/outputs/output_{model}.jsonl'
    if not os.path.exists(path):
        return f"No output found for {model}.", 404
    return send_file(path, as_attachment=True)

@app.route('/logout')
def logout():
    if 'user_id' in session:
        last_session = SessionLog.query.filter_by(user_id=session['user_id']).order_by(SessionLog.id.desc()).first()
        if last_session:
            last_session.logout_time = datetime.now(timezone.utc)
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

        if not User.query.filter_by(username='admin').first():
            admin_user = User(
                username='admin',
                password=generate_password_hash('admin123'),
                is_admin=True
            )
            db.session.add(admin_user)
            db.session.commit()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
