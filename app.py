import os, json, time, threading, sqlite3, secrets, hmac
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from cryptography.fernet import Fernet, InvalidToken
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(APP_DIR, "bot.sqlite3"))
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-before-deploying")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

def get_cipher():
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is missing. Generate one and add it to environment variables.")
    return Fernet(key.encode())

def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            channel_id TEXT NOT NULL UNIQUE,
            channel_title TEXT NOT NULL,
            token_enc TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS settings (
            profile_id INTEGER PRIMARY KEY,
            welcome_enabled INTEGER NOT NULL DEFAULT 1,
            welcome_text TEXT NOT NULL DEFAULT 'Welcome to the stream, {name}! Enjoy the live!',
            reminder_enabled INTEGER NOT NULL DEFAULT 0,
            reminder_text TEXT NOT NULL DEFAULT 'If you are enjoying the stream, remember to subscribe!',
            reminder_minutes INTEGER NOT NULL DEFAULT 15,
            commands_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        )""")

init_db()
workers = {}
worker_lock = threading.Lock()
bot_status = {}

def get_profile(profile_id):
    with db() as con:
        return con.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()

def get_settings(profile_id):
    with db() as con:
        row = con.execute("SELECT * FROM settings WHERE profile_id=?", (profile_id,)).fetchone()
    if row:
        return dict(row)
    return {
        "welcome_enabled": 1,
        "welcome_text": "Welcome to the stream, {name}! Enjoy the live!",
        "reminder_enabled": 0,
        "reminder_text": "If you are enjoying the stream, remember to subscribe!",
        "reminder_minutes": 15,
        "commands_json": "{}"
    }

def youtube_for_profile(profile):
    raw = get_cipher().decrypt(profile["token_enc"].encode()).decode()
    token_data = json.loads(raw)
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)

def credentials_config():
    raw = os.environ.get("GOOGLE_CLIENT_SECRETS_JSON", "").strip()
    if raw:
        return json.loads(raw)
    path = os.environ.get("GOOGLE_CLIENT_SECRETS_FILE", "client_secret.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    raise RuntimeError("Google OAuth client credentials are not configured. See README.md.")

def make_flow(state=None):
    config = credentials_config()
    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI", request.url_root.rstrip("/") + "/oauth/callback")
    flow = Flow.from_client_config(config, scopes=SCOPES, state=state)
    flow.redirect_uri = redirect_uri
    return flow

def safe_int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default

@app.route("/")
def index():
    with db() as con:
        profiles = con.execute("SELECT id,label,channel_id,channel_title,created_at FROM profiles ORDER BY id").fetchall()
    states = {}
    for p in profiles:
        states[str(p["id"])] = bot_status.get(p["id"], {"running": False, "message": "Stopped"})
    return render_template("index.html", profiles=profiles, states=states)

@app.route("/oauth/start", methods=["POST"])
def oauth_start():
    label = (request.form.get("label") or "").strip()[:40]
    if not label:
        flash("Enter a name for this channel account first.", "error")
        return redirect(url_for("index"))
    try:
        flow = make_flow()
        authorization_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )
        session["oauth_state"] = state
        session["oauth_label"] = label
        return redirect(authorization_url)
    except Exception as exc:
        flash(f"OAuth setup error: {exc}", "error")
        return redirect(url_for("index"))

@app.route("/oauth/callback")
def oauth_callback():
    try:
        state = session.pop("oauth_state", None)
        label = session.pop("oauth_label", "YouTube channel")
        if not state or request.args.get("state") != state:
            raise RuntimeError("OAuth state did not match. Please connect again.")
        flow = make_flow(state=state)
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        response = youtube.channels().list(part="id,snippet", mine=True).execute()
        items = response.get("items", [])
        if not items:
            raise RuntimeError("No YouTube channel was returned by this Google account.")
        channel = items[0]
        token_json = creds.to_json()
        encrypted = get_cipher().encrypt(token_json.encode()).decode()
        with db() as con:
            con.execute("""INSERT INTO profiles(label,channel_id,channel_title,token_enc,created_at)
                VALUES(?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET
                label=excluded.label, channel_title=excluded.channel_title, token_enc=excluded.token_enc""",
                (label, channel["id"], channel["snippet"]["title"], encrypted,
                 datetime.now(timezone.utc).isoformat()))
            row = con.execute("SELECT id FROM profiles WHERE channel_id=?", (channel["id"],)).fetchone()
            con.execute("INSERT OR IGNORE INTO settings(profile_id) VALUES(?)", (row["id"],))
        flash(f"Connected: {channel['snippet']['title']}. You can connect your second Google account separately.", "success")
    except Exception as exc:
        flash(f"Could not connect YouTube: {exc}", "error")
    return redirect(url_for("index"))

@app.route("/settings/<int:profile_id>", methods=["GET", "POST"])
def settings(profile_id):
    profile = get_profile(profile_id)
    if not profile:
        return "Channel profile not found", 404
    if request.method == "POST":
        welcome_enabled = 1 if request.form.get("welcome_enabled") == "on" else 0
        reminder_enabled = 1 if request.form.get("reminder_enabled") == "on" else 0
        welcome_text = (request.form.get("welcome_text") or "").strip()[:180]
        reminder_text = (request.form.get("reminder_text") or "").strip()[:180]
        reminder_minutes = safe_int(request.form.get("reminder_minutes"), 15, 5, 180)
        commands_text = (request.form.get("commands_json") or "{}").strip()
        try:
            commands = json.loads(commands_text)
            if not isinstance(commands, dict) or len(commands) > 30:
                raise ValueError("Commands must be a JSON object with up to 30 entries.")
            commands = {str(k).lower().strip()[:24]: str(v)[:180] for k, v in commands.items()}
            if any(not k.startswith("!") for k in commands):
                raise ValueError("Every custom command key must begin with !")
        except Exception as exc:
            flash(f"Custom commands are invalid: {exc}", "error")
            return redirect(url_for("settings", profile_id=profile_id))
        with db() as con:
            con.execute("""INSERT INTO settings(profile_id,welcome_enabled,welcome_text,reminder_enabled,
                reminder_text,reminder_minutes,commands_json) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(profile_id) DO UPDATE SET welcome_enabled=excluded.welcome_enabled,
                welcome_text=excluded.welcome_text, reminder_enabled=excluded.reminder_enabled,
                reminder_text=excluded.reminder_text, reminder_minutes=excluded.reminder_minutes,
                commands_json=excluded.commands_json""",
                (profile_id, welcome_enabled, welcome_text or "Welcome, {name}!", reminder_enabled,
                 reminder_text or "Thanks for watching!", reminder_minutes, json.dumps(commands)))
        flash("Bot settings saved.", "success")
        return redirect(url_for("settings", profile_id=profile_id))
    s = get_settings(profile_id)
    return render_template("settings.html", profile=profile, settings=s,
                           commands_pretty=json.dumps(json.loads(s["commands_json"]), indent=2))

@app.route("/bot/<int:profile_id>/start", methods=["POST"])
def start_bot(profile_id):
    profile = get_profile(profile_id)
    if not profile:
        return jsonify(ok=False, error="Profile not found"), 404
    with worker_lock:
        if profile_id in workers and workers[profile_id].is_alive():
            return jsonify(ok=True, message="Already running")
        stop_event = threading.Event()
        thread = threading.Thread(target=bot_worker, args=(profile_id, stop_event), daemon=True)
        workers[profile_id] = thread
        bot_status[profile_id] = {"running": True, "message": "Starting…"}
        thread.start()
    return jsonify(ok=True, message="Bot started. Keep this hosted service running.")

@app.route("/bot/<int:profile_id>/stop", methods=["POST"])
def stop_bot(profile_id):
    with worker_lock:
        event = getattr(workers.get(profile_id), "stop_event", None)
        # Events are stored separately on the thread for a simple stop control.
        thread = workers.get(profile_id)
        if thread and hasattr(thread, "_stop_event"):
            thread._stop_event.set()
        bot_status[profile_id] = {"running": False, "message": "Stop requested"}
    return jsonify(ok=True, message="Stop requested")

@app.route("/bot/status")
def status():
    return jsonify({str(k): v for k, v in bot_status.items()})

@app.route("/profile/<int:profile_id>/delete", methods=["POST"])
def delete_profile(profile_id):
    with worker_lock:
        thread = workers.get(profile_id)
        if thread and hasattr(thread, "_stop_event"):
            thread._stop_event.set()
    with db() as con:
        con.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
    bot_status.pop(profile_id, None)
    flash("Channel connection removed from this bot.", "success")
    return redirect(url_for("index"))

def send_message(youtube, live_chat_id, message):
    message = str(message).strip()[:200]
    if not message:
        return
    youtube.liveChatMessages().insert(
        part="snippet",
        body={"snippet": {"liveChatId": live_chat_id, "type": "textMessageEvent",
                          "textMessageDetails": {"messageText": message}}}
    ).execute()

def bot_worker(profile_id, stop_event):
    thread = threading.current_thread()
    thread._stop_event = stop_event
    greeted = set()
    page_token = None
    last_reminder = time.time()
    try:
        profile = get_profile(profile_id)
        youtube = youtube_for_profile(profile)
        bot_status[profile_id] = {"running": True, "message": "Looking for an active livestream…"}
        while not stop_event.is_set():
            try:
                live = youtube.liveBroadcasts().list(
                    part="id,snippet,contentDetails", broadcastStatus="active", mine=True, maxResults=5
                ).execute()
                items = live.get("items", [])
                if not items:
                    bot_status[profile_id] = {"running": True, "message": "Waiting for this channel to go live…"}
                    stop_event.wait(15)
                    continue
                live_chat_id = items[0].get("snippet", {}).get("liveChatId")
                if not live_chat_id:
                    bot_status[profile_id] = {"running": True, "message": "Live chat is disabled or unavailable."}
                    stop_event.wait(15)
                    continue
                bot_status[profile_id] = {"running": True, "message": "Connected to live chat"}
                settings_data = get_settings(profile_id)
                try:
                    commands = json.loads(settings_data["commands_json"])
                except Exception:
                    commands = {}
                if settings_data["reminder_enabled"] and time.time() - last_reminder >= settings_data["reminder_minutes"] * 60:
                    send_message(youtube, live_chat_id, settings_data["reminder_text"])
                    last_reminder = time.time()
                params = {"liveChatId": live_chat_id, "part": "id,snippet,authorDetails",
                          "maxResults": 200}
                if page_token:
                    params["pageToken"] = page_token
                result = youtube.liveChatMessages().list(**params).execute()
                page_token = result.get("nextPageToken", page_token)
                for item in result.get("items", []):
                    snippet = item.get("snippet", {})
                    author = item.get("authorDetails", {})
                    author_id = author.get("channelId", "")
                    author_name = author.get("displayName", "viewer")
                    if author_id and author_id == profile["channel_id"]:
                        continue
                    if snippet.get("type") != "textMessageEvent":
                        continue
                    message = snippet.get("textMessageDetails", {}).get("messageText", "").strip()
                    if settings_data["welcome_enabled"] and author_id and author_id not in greeted:
                        greeted.add(author_id)
                        send_message(youtube, live_chat_id, settings_data["welcome_text"].replace("{name}", author_name))
                    lowered = message.lower().split()[0] if message else ""
                    if lowered == "!help":
                        send_message(youtube, live_chat_id, "Commands: !help" + (", " + ", ".join(commands.keys()) if commands else ""))
                    elif lowered in commands:
                        send_message(youtube, live_chat_id, commands[lowered])
                wait_ms = safe_int(result.get("pollingIntervalMillis"), 5000, 3000, 30000)
                stop_event.wait(wait_ms / 1000)
            except HttpError as exc:
                code = getattr(exc.resp, "status", 0)
                bot_status[profile_id] = {"running": True, "message": f"YouTube API error ({code}); retrying shortly."}
                stop_event.wait(20 if code in (403, 429) else 8)
            except Exception as exc:
                bot_status[profile_id] = {"running": True, "message": f"Connection issue: {str(exc)[:100]}"}
                stop_event.wait(10)
    except Exception as exc:
        bot_status[profile_id] = {"running": False, "message": f"Could not start: {str(exc)[:120]}"}
    finally:
        bot_status[profile_id] = {"running": False, "message": "Stopped"}

@app.route("/health")
def health():
    return jsonify(status="ok")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
