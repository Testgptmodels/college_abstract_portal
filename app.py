from flask import Flask, render_template, request, redirect, session, jsonify
import os
import jsonlines
from pathlib import Path
import uuid
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = "your-secret-key"

# === Persistent storage paths ===
BASE_PATH = "/var/data"
INPUT_FILE = os.path.join(BASE_PATH, "inputs", "arxiv_2000_2025_all_final.jsonl")
OUTPUT_DIR = os.path.join(BASE_PATH, "outputs")
PROGRESS_DIR = os.path.join(BASE_PATH, "progress")
USER_LOG_DIR = os.path.join(BASE_PATH, "user_logs")

# === Ensure required folders exist ===
for folder in [os.path.dirname(INPUT_FILE), OUTPUT_DIR, PROGRESS_DIR, USER_LOG_DIR]:
    os.makedirs(folder, exist_ok=True)

# === In-memory user DB ===
USERS = {}

@app.route("/")
def index():
    if "username" in session:
        return redirect("/user_dashboard")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        USERS[username] = password
        return redirect("/")
    return render_template("register.html")

@app.route("/login", methods=["POST"])
def login():
    username = request.form["username"]
    password = request.form["password"]
    if username in USERS and USERS[username] == password:
        session["username"] = username
        return redirect("/user_dashboard")
    return "Invalid credentials", 403

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/user_dashboard")
def user_dashboard():
    if "username" not in session:
        return redirect("/")
    return render_template("user_dashboard.html")

@app.route("/submit")
def submit():
    if "username" not in session:
        return redirect("/")
    return render_template("abstract_submit.html")

@app.route("/get_next/<model>")
def get_next(model):
    if "username" not in session:
        return "Unauthorized", 401

    username = session["username"]
    user_progress_file = os.path.join(PROGRESS_DIR, f"{username}_{model}.json")

    # Load progress
    assigned = {}
    if os.path.exists(user_progress_file):
        with open(user_progress_file, "r", encoding="utf-8") as f:
            assigned = json.load(f)

    # Load shared input file
    next_prompt = None
    with jsonlines.open(INPUT_FILE) as reader:
        for idx, obj in enumerate(reader):
            prompt_id = str(obj.get("id") or idx)
            if prompt_id not in assigned:
                next_prompt = obj
                next_prompt["id"] = prompt_id
                break

    if not next_prompt:
        return jsonify({"message": "✅ All prompts completed!"})

    return jsonify({
        "prompt": next_prompt,
        "completed": len(assigned)
    })

@app.route("/submit/<model>", methods=["POST"])
def submit_response(model):
    if "username" not in session:
        return "Unauthorized", 401

    data = request.get_json()
    username = session["username"]

    title = data.get("title", "").strip()
    abstract = data.get("abstract", "").strip()
    think = data.get("think", "").strip()
    prompt_id = str(data.get("id"))

    if not title or not abstract or not prompt_id:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    entry_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()

    entry = {
        "uuid": entry_id,
        "id": prompt_id,
        "title": title,
        "abstract": abstract,
        "think": think,
        "submitted_by": username,
        "submitted_at": timestamp,
        "model": model
    }

    # Save output
    output_path = os.path.join(OUTPUT_DIR, f"output_{model}.jsonl")
    with jsonlines.open(output_path, "a") as writer:
        writer.write(entry)

    # Update progress
    progress_file = os.path.join(PROGRESS_DIR, f"{username}_{model}.json")
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            progress = json.load(f)
    progress[prompt_id] = entry_id
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress, f)

    # Log per user
    user_log_file = os.path.join(USER_LOG_DIR, f"{username}_{model}.jsonl")
    with jsonlines.open(user_log_file, "a") as writer:
        writer.write(entry)

    return jsonify({"status": "success", "id": entry_id})

if __name__ == "__main__":
    app.run(debug=True, port=10000)
