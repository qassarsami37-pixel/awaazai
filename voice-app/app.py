import os, io, re, wave, base64, json, sqlite3, secrets, time, smtplib, hashlib, hmac
from functools import wraps
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from flask import (Flask, render_template, request, jsonify, session,
                   redirect, url_for, flash, g, Response, make_response)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", secrets.token_hex(32))

# ── Sarvam AI ──────────────────────────────────────────────────────────────
SARVAM_API_KEY        = os.environ.get("SARVAM_API_KEY", "")
SARVAM_TTS_URL        = "https://api.sarvam.ai/text-to-speech"
SARVAM_TRANSLIT_URL   = "https://api.sarvam.ai/transliterate"
SARVAM_MODEL          = "bulbul:v2"   # v1 deprecated — do NOT change

# ── Razorpay (scaffold — add RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET secrets) ─
RAZORPAY_KEY_ID     = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# ── Google OAuth (scaffold — add GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) ──
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

# ── Plans ───────────────────────────────────────────────────────────────────
PLANS = {
    "free":     {"name": "Free",     "price": 0,   "daily_limit": 10,   "api": False},
    "pro":      {"name": "Pro",      "price": 99,  "daily_limit": None, "api": False},
    "business": {"name": "Business", "price": 299, "daily_limit": None, "api": True},
}

# ── Speakers (bulbul:v2 confirmed valid names) ──────────────────────────────
_FEMALE = [
    {"id": "anushka", "name": "Anushka", "gender": "female"},
    {"id": "manisha", "name": "Manisha", "gender": "female"},
    {"id": "vidya",   "name": "Vidya",   "gender": "female"},
    {"id": "priya",   "name": "Priya",   "gender": "female"},
    {"id": "neha",    "name": "Neha",    "gender": "female"},
    {"id": "shruti",  "name": "Shruti",  "gender": "female"},
]
_MALE = [
    {"id": "abhilash", "name": "Abhilash", "gender": "male"},
    {"id": "aditya",   "name": "Aditya",   "gender": "male"},
    {"id": "rahul",    "name": "Rahul",    "gender": "male"},
    {"id": "rohan",    "name": "Rohan",    "gender": "male"},
    {"id": "amit",     "name": "Amit",     "gender": "male"},
    {"id": "kabir",    "name": "Kabir",    "gender": "male"},
]
_ALL_SPEAKERS    = _FEMALE + _MALE
VALID_SPEAKER_IDS = {s["id"] for s in _ALL_SPEAKERS}

SPEAKERS = {lang: _ALL_SPEAKERS for lang in [
    "hi-IN","bn-IN","gu-IN","kn-IN","ml-IN",
    "mr-IN","od-IN","pa-IN","ta-IN","te-IN","en-IN",
]}
VALID_LANGUAGES = set(SPEAKERS.keys())
MAX_TEXT_LEN    = 5000
DB_PATH         = os.path.join(os.path.dirname(__file__), "awaazai.db")


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════════
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        email         TEXT    UNIQUE NOT NULL,
        password_hash TEXT,
        name          TEXT    DEFAULT '',
        plan          TEXT    DEFAULT 'free',
        verified      INTEGER DEFAULT 0,
        verify_token  TEXT,
        reset_token   TEXT,
        reset_expires REAL,
        created_at    TEXT    DEFAULT CURRENT_TIMESTAMP,
        referral_code TEXT    UNIQUE,
        referred_by   TEXT,
        bonus_credits INTEGER DEFAULT 0,
        api_key       TEXT    UNIQUE,
        is_admin      INTEGER DEFAULT 0,
        google_id     TEXT
    );
    CREATE TABLE IF NOT EXISTS voice_history (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        text_preview TEXT,
        full_text    TEXT,
        language     TEXT,
        speaker      TEXT,
        audio_b64    TEXT,
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS daily_usage (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        usage_date TEXT    NOT NULL,
        count      INTEGER DEFAULT 0,
        UNIQUE(user_id, usage_date)
    );
    CREATE TABLE IF NOT EXISTS rate_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        identifier   TEXT    NOT NULL,
        requested_at REAL    NOT NULL
    );
    CREATE TABLE IF NOT EXISTS contact_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT,
        email      TEXT,
        message    TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()

init_db()


# ── Context processor — inject globals into every template ─────────────────
@app.context_processor
def inject_globals():
    return {
        "google_enabled": bool(GOOGLE_CLIENT_ID),
        "razorpay_enabled": bool(RAZORPAY_KEY_ID),
    }


# ══════════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if "user_id" not in session:
            if request.is_json:
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapped

def admin_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if "user_id" not in session or not session.get("is_admin"):
            return redirect(url_for("index"))
        return f(*a, **kw)
    return wrapped

def current_user():
    if "user_id" not in session:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def make_referral_code():
    return secrets.token_urlsafe(6).upper()

def make_api_key():
    return "awz_" + secrets.token_hex(24)

def check_rate_limit(identifier, max_per_min=3):
    db = get_db()
    since = time.time() - 60
    cnt = db.execute(
        "SELECT COUNT(*) FROM rate_log WHERE identifier=? AND requested_at>?",
        (str(identifier), since)
    ).fetchone()[0]
    return cnt >= max_per_min

def log_request(identifier):
    db = get_db()
    db.execute("INSERT INTO rate_log(identifier,requested_at) VALUES(?,?)",
               (str(identifier), time.time()))
    db.execute("DELETE FROM rate_log WHERE requested_at<?", (time.time()-120,))
    db.commit()

def get_daily_usage(user_id):
    db = get_db()
    row = db.execute(
        "SELECT count FROM daily_usage WHERE user_id=? AND usage_date=?",
        (user_id, date.today().isoformat())
    ).fetchone()
    return row["count"] if row else 0

def bump_daily_usage(user_id):
    db = get_db()
    db.execute("""
        INSERT INTO daily_usage(user_id,usage_date,count) VALUES(?,?,1)
        ON CONFLICT(user_id,usage_date) DO UPDATE SET count=count+1
    """, (user_id, date.today().isoformat()))
    db.commit()

def save_history(user_id, text, language, speaker, audio_b64):
    db = get_db()
    db.execute("""
        INSERT INTO voice_history(user_id,text_preview,full_text,language,speaker,audio_b64)
        VALUES(?,?,?,?,?,?)
    """, (user_id, text[:100], text, language, speaker, audio_b64))
    db.commit()

def send_email(to, subject, html):
    srv = os.environ.get("MAIL_SERVER", "")
    if not srv:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = os.environ.get("MAIL_FROM", os.environ.get("MAIL_USERNAME", "noreply@awaazai.in"))
        msg["To"]      = to
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(srv, int(os.environ.get("MAIL_PORT", 587))) as s:
            s.starttls()
            s.login(os.environ.get("MAIL_USERNAME", ""), os.environ.get("MAIL_PASSWORD", ""))
            s.sendmail(msg["From"], to, msg.as_string())
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# TTS HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def split_text(text, max_len=490):
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    segments, current = [], ""
    for part in re.split(r'(?<=[।.!?;])\s*', text):
        if not part:
            continue
        if len(current) + len(part) <= max_len:
            current += part
        else:
            if current:
                segments.append(current.strip())
            if len(part) > max_len:
                while len(part) > max_len:
                    cut = part.rfind(",", 0, max_len)
                    if cut == -1 or cut < max_len // 3:
                        cut = max_len
                    segments.append(part[:cut].strip())
                    part = part[cut:].lstrip(", ")
                current = part
            else:
                current = part
    if current.strip():
        segments.append(current.strip())
    return [s for s in segments if s]

def combine_wav_b64(b64_list, silence_ms=250, sample_rate=22050):
    if len(b64_list) == 1:
        return b64_list[0]
    silence    = b"\x00" * int(sample_rate * silence_ms / 1000) * 2
    all_frames = []
    params     = None
    for i, b64 in enumerate(b64_list):
        raw = base64.b64decode(b64)
        with wave.open(io.BytesIO(raw)) as w:
            if params is None:
                params = w.getparams()
            all_frames.append(w.readframes(w.getnframes()))
        if i < len(b64_list) - 1:
            all_frames.append(silence)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        for frames in all_frames:
            w.writeframes(frames)
    out.seek(0)
    return base64.b64encode(out.read()).decode()


# ══════════════════════════════════════════════════════════════════════════════
# PAGES
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return render_template("landing.html", user=current_user())

@app.route("/studio")
def studio():
    u = current_user()
    daily = get_daily_usage(u["id"]) if u else 0
    limit  = PLANS.get(u["plan"] if u else "free", PLANS["free"])["daily_limit"] or 9999
    return render_template("studio.html", user=u, speakers_json=SPEAKERS,
                           daily_usage=daily, daily_limit=limit,
                           razorpay_key=RAZORPAY_KEY_ID)

@app.route("/pricing")
def pricing():
    return render_template("pricing.html", user=current_user(),
                           razorpay_key=RAZORPAY_KEY_ID)

@app.route("/dashboard")
@login_required
def dashboard():
    u  = current_user()
    db = get_db()
    hist = db.execute(
        "SELECT * FROM voice_history WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
        (u["id"],)
    ).fetchall()
    total_gen = db.execute(
        "SELECT SUM(count) FROM daily_usage WHERE user_id=?", (u["id"],)
    ).fetchone()[0] or 0
    today_cnt = get_daily_usage(u["id"])
    plan_info = PLANS.get(u["plan"], PLANS["free"])
    limit     = plan_info["daily_limit"] or 9999
    # 7-day usage chart
    rows = db.execute("""
        SELECT usage_date, count FROM daily_usage
        WHERE user_id=? AND usage_date >= date('now','-6 days')
        ORDER BY usage_date
    """, (u["id"],)).fetchall()
    chart = {r["usage_date"]: r["count"] for r in rows}
    return render_template("dashboard.html", user=u, history=hist,
                           total_gen=total_gen, today_cnt=today_cnt,
                           plan_info=plan_info, daily_limit=limit, chart=chart)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    u  = current_user()
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        db.execute("UPDATE users SET name=? WHERE id=?", (name, u["id"]))
        db.commit()
        session["name"] = name
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=u, plans=PLANS)

@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    users     = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    total_gen = db.execute("SELECT SUM(count) FROM daily_usage").fetchone()[0] or 0
    today_gen = db.execute(
        "SELECT SUM(count) FROM daily_usage WHERE usage_date=?",
        (date.today().isoformat(),)
    ).fetchone()[0] or 0
    msgs      = db.execute(
        "SELECT * FROM contact_messages ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    return render_template("admin.html", user=current_user(), users=users,
                           total_gen=total_gen, today_gen=today_gen, msgs=msgs)

@app.route("/about")
def about():
    return render_template("about.html", user=current_user())

@app.route("/blog")
def blog():
    return render_template("blog.html", user=current_user())

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "")
        email = request.form.get("email", "")
        msg  = request.form.get("message", "")
        db   = get_db()
        db.execute("INSERT INTO contact_messages(name,email,message) VALUES(?,?,?)",
                   (name, email, msg))
        db.commit()
        flash("Message sent! We'll reply soon.", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html", user=current_user())

@app.route("/terms")
def terms():
    return render_template("terms.html", user=current_user())

@app.route("/privacy")
def privacy():
    return render_template("privacy.html", user=current_user())


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("studio"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name  = request.form.get("name", "").strip()
        pw    = request.form.get("password", "")
        ref   = request.form.get("referral", "").strip().upper()
        if not email or not pw or len(pw) < 6:
            flash("Please fill all fields. Password must be ≥ 6 chars.", "error")
            return render_template("auth/signup.html")
        db = get_db()
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            flash("Email already registered. Please log in.", "error")
            return render_template("auth/signup.html")
        token   = secrets.token_urlsafe(32)
        ref_code= make_referral_code()
        referred_by = None
        if ref:
            referrer = db.execute("SELECT id FROM users WHERE referral_code=?", (ref,)).fetchone()
            if referrer:
                referred_by = ref
                db.execute("UPDATE users SET bonus_credits=bonus_credits+10 WHERE id=?",
                           (referrer["id"],))
        db.execute("""
            INSERT INTO users(email,password_hash,name,verify_token,referral_code,referred_by)
            VALUES(?,?,?,?,?,?)
        """, (email, generate_password_hash(pw), name, token, ref_code, referred_by))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        send_email(email, "Verify your AwaazAI account",
            f"""<h2>Welcome to AwaazAI!</h2>
            <p>Click to verify your email:</p>
            <a href="https://{''.join(os.environ.get('REPLIT_DOMAINS','localhost').split(',')[:1])}/verify/{token}" style="background:#f97316;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">Verify Email</a>""")
        session["user_id"]  = user["id"]
        session["email"]    = email
        session["name"]     = name
        session["plan"]     = "free"
        session["is_admin"] = False
        flash("Account created! Check your email to verify.", "success")
        return redirect(url_for("studio"))
    return render_template("auth/signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("studio"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        db    = get_db()
        user  = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], pw):
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html")
        session["user_id"]  = user["id"]
        session["email"]    = user["email"]
        session["name"]     = user["name"]
        session["plan"]     = user["plan"]
        session["is_admin"] = bool(user["is_admin"])
        nxt = request.args.get("next", url_for("studio"))
        return redirect(nxt)
    return render_template("auth/login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/verify/<token>")
def verify_email(token):
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE verify_token=?", (token,)).fetchone()
    if not user:
        flash("Invalid or expired verification link.", "error")
        return redirect(url_for("login"))
    db.execute("UPDATE users SET verified=1, verify_token=NULL WHERE id=?", (user["id"],))
    db.commit()
    flash("Email verified! You're all set.", "success")
    return redirect(url_for("studio"))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        db    = get_db()
        user  = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user:
            token   = secrets.token_urlsafe(32)
            expires = time.time() + 3600
            db.execute("UPDATE users SET reset_token=?, reset_expires=? WHERE id=?",
                       (token, expires, user["id"]))
            db.commit()
            domain = os.environ.get("REPLIT_DOMAINS", "localhost").split(",")[0]
            send_email(email, "Reset your AwaazAI password",
                f"""<h2>Password Reset</h2>
                <p>Click to reset your password (link expires in 1 hour):</p>
                <a href="https://{domain}/reset-password/{token}" style="background:#f97316;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">Reset Password</a>""")
        flash("If that email is registered, a reset link has been sent.", "success")
        return redirect(url_for("forgot_password"))
    return render_template("auth/forgot.html")

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    db   = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE reset_token=? AND reset_expires>?",
        (token, time.time())
    ).fetchone()
    if not user:
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        if len(pw) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("auth/reset.html", token=token)
        db.execute("UPDATE users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?",
                   (generate_password_hash(pw), user["id"]))
        db.commit()
        flash("Password reset! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("auth/reset.html", token=token)

@app.route("/auth/google")
def google_auth():
    if not GOOGLE_CLIENT_ID:
        flash("Google login is not configured.", "error")
        return redirect(url_for("login"))
    domain = os.environ.get("REPLIT_DOMAINS", "localhost").split(",")[0]
    redirect_uri = f"https://{domain}/auth/google/callback"
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    params = "&".join([
        f"client_id={GOOGLE_CLIENT_ID}",
        f"redirect_uri={redirect_uri}",
        "response_type=code",
        "scope=openid email profile",
        f"state={state}",
    ])
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.route("/auth/google/callback")
def google_callback():
    if not GOOGLE_CLIENT_ID or request.args.get("state") != session.pop("oauth_state", None):
        flash("Google login failed.", "error")
        return redirect(url_for("login"))
    domain = os.environ.get("REPLIT_DOMAINS", "localhost").split(",")[0]
    redirect_uri = f"https://{domain}/auth/google/callback"
    code = request.args.get("code", "")
    try:
        tok_resp = requests.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        }, timeout=10).json()
        info = requests.get("https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {tok_resp['access_token']}"}, timeout=10).json()
    except Exception:
        flash("Google login failed.", "error")
        return redirect(url_for("login"))
    google_id = info.get("sub", "")
    email     = info.get("email", "").lower()
    name      = info.get("name", "")
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email=? OR google_id=?",
                      (email, google_id)).fetchone()
    if not user:
        ref_code = make_referral_code()
        db.execute("""INSERT INTO users(email,name,google_id,verified,referral_code)
                      VALUES(?,?,?,1,?)""", (email, name, google_id, ref_code))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    else:
        db.execute("UPDATE users SET google_id=?, verified=1 WHERE id=?",
                   (google_id, user["id"]))
        db.commit()
    session["user_id"]  = user["id"]
    session["email"]    = user["email"]
    session["name"]     = user["name"] or name
    session["plan"]     = user["plan"]
    session["is_admin"] = bool(user["is_admin"])
    return redirect(url_for("studio"))


# ══════════════════════════════════════════════════════════════════════════════
# TTS API
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/api/tts", methods=["POST"])
def text_to_speech():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    u        = current_user()
    user_id  = u["id"] if u else None
    ip       = request.remote_addr
    ident    = user_id or ip

    # Rate limit: 3 requests/minute
    if check_rate_limit(str(ident)):
        return jsonify({"error": "Rate limit: max 3 requests per minute. Please wait."}), 429
    log_request(str(ident))

    # Daily credit limit (free plan)
    if u:
        plan      = PLANS.get(u["plan"], PLANS["free"])
        day_limit = plan["daily_limit"]
        if day_limit is not None:
            used = get_daily_usage(user_id)
            bonus = u["bonus_credits"]
            if used >= day_limit + bonus:
                return jsonify({
                    "error": f"Daily limit of {day_limit + bonus} voices reached. Upgrade for unlimited.",
                    "upgrade": True
                }), 403
    else:
        # Guest: 3 tries per session
        session["guest_count"] = session.get("guest_count", 0) + 1
        if session["guest_count"] > 3:
            return jsonify({"error": "Sign up for free to generate more voices.", "signup": True}), 403

    text     = data.get("text", "").strip()
    language = data.get("language", "hi-IN")
    speaker  = data.get("speaker", "anushka")
    pitch    = max(-20, min(20,  int(round(float(data.get("pitch",    0))))))
    pace     = max(0.5, min(2.0, float(data.get("pace",   1.0))))
    loudness = max(0.1, min(3.0, float(data.get("loudness", 1.5))))

    if not text:
        return jsonify({"error": "Text is required"}), 400
    if len(text) > MAX_TEXT_LEN:
        return jsonify({"error": f"Text must be ≤ {MAX_TEXT_LEN:,} chars."}), 400
    if not SARVAM_API_KEY:
        return jsonify({"error": "SARVAM_API_KEY not configured"}), 500
    if language not in VALID_LANGUAGES:
        return jsonify({"error": f"Unsupported language: {language}"}), 400
    if speaker not in VALID_SPEAKER_IDS:
        speaker = "anushka"

    headers  = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    segments = split_text(text)
    collected= []

    for seg in segments:
        payload = {
            "inputs": [seg],
            "target_language_code": language,
            "speaker": speaker,
            "pitch": pitch,
            "pace": pace,
            "loudness": loudness,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
            "model": SARVAM_MODEL,
        }
        try:
            resp = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=30)
        except requests.exceptions.Timeout:
            return jsonify({"error": "Sarvam AI timed out. Try again."}), 504
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Network error: {e}"}), 502

        if resp.status_code != 200:
            try:
                err = resp.json()
            except Exception:
                err = {"message": resp.text}
            return jsonify({"error": err.get("message", f"Sarvam error {resp.status_code}")}), resp.status_code

        result = resp.json()
        audios = result.get("audios", [])
        if not audios:
            return jsonify({"error": "No audio from Sarvam API"}), 500
        collected.append(audios[0])

    combined = combine_wav_b64(collected)

    if user_id:
        bump_daily_usage(user_id)
        save_history(user_id, text, language, speaker, combined)

    used_after = get_daily_usage(user_id) if user_id else None
    plan_info  = PLANS.get(u["plan"] if u else "free", PLANS["free"])
    limit      = plan_info["daily_limit"]

    return jsonify({
        "audio": combined,
        "segments": len(segments),
        "daily_used": used_after,
        "daily_limit": limit,
    })


@app.route("/api/v1/tts", methods=["POST"])
def api_v1_tts():
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        return jsonify({"error": "X-API-Key header required"}), 401
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE api_key=?", (api_key,)).fetchone()
    if not user:
        return jsonify({"error": "Invalid API key"}), 401
    if user["plan"] not in ("pro", "business"):
        return jsonify({"error": "API access requires Pro or Business plan"}), 403
    data = request.get_json() or {}
    text     = data.get("text", "").strip()
    language = data.get("language", "hi-IN")
    speaker  = data.get("speaker", "anushka")
    pitch    = max(-20, min(20,  int(round(float(data.get("pitch",  0))))))
    pace     = max(0.5, min(2.0, float(data.get("pace",   1.0))))
    loudness = max(0.1, min(3.0, float(data.get("loudness", 1.5))))
    if not text:
        return jsonify({"error": "text is required"}), 400
    if speaker not in VALID_SPEAKER_IDS:
        speaker = "anushka"
    headers  = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    segments = split_text(text)
    collected= []
    for seg in segments:
        resp = requests.post(SARVAM_TTS_URL, headers=headers, json={
            "inputs": [seg], "target_language_code": language,
            "speaker": speaker, "pitch": pitch, "pace": pace,
            "loudness": loudness, "speech_sample_rate": 22050,
            "enable_preprocessing": True, "model": SARVAM_MODEL,
        }, timeout=30)
        if resp.status_code != 200:
            return jsonify({"error": resp.json().get("message", "Sarvam error")}), 502
        collected.append(resp.json()["audios"][0])
    return jsonify({"audio": combine_wav_b64(collected), "segments": len(segments)})


@app.route("/api/transliterate", methods=["POST"])
def transliterate():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    text = data.get("text", "").strip()
    lang = data.get("language", "hi-IN")
    if not text:
        return jsonify({"transliterated": ""}), 200
    if not SARVAM_API_KEY:
        return jsonify({"error": "SARVAM_API_KEY not configured"}), 500
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    payload = {
        "input": text, "source_language_code": "en-Latn",
        "target_language_code": lang, "speaker_gender": "Female",
        "mode": "classic-colloquial", "enable_preprocessing": False,
        "numerals_format": "international",
    }
    try:
        resp = requests.post(SARVAM_TRANSLIT_URL, json=payload, headers=headers, timeout=15)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Transliteration timed out"}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502
    if resp.status_code != 200:
        err = resp.json()
        return jsonify({"error": err.get("message", f"Sarvam error {resp.status_code}")}), resp.status_code
    return jsonify({"transliterated": resp.json().get("transliterated_text", text)})


@app.route("/api/speakers/<language>")
def get_speakers(language):
    return jsonify(SPEAKERS.get(language, _ALL_SPEAKERS))


@app.route("/api/history")
@login_required
def get_history():
    u  = current_user()
    db = get_db()
    rows = db.execute(
        "SELECT id, text_preview, language, speaker, created_at, audio_b64 FROM voice_history WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (u["id"],)
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/history/<int:item_id>", methods=["DELETE"])
@login_required
def delete_history(item_id):
    u  = current_user()
    db = get_db()
    db.execute("DELETE FROM voice_history WHERE id=? AND user_id=?", (item_id, u["id"]))
    db.commit()
    return jsonify({"ok": True})


# ── Payment (Razorpay) ──────────────────────────────────────────────────────
@app.route("/api/payment/create-order", methods=["POST"])
@login_required
def create_order():
    if not RAZORPAY_KEY_ID:
        return jsonify({"error": "Payment not configured"}), 503
    data  = request.get_json() or {}
    plan  = data.get("plan", "pro")
    price = PLANS.get(plan, PLANS["pro"])["price"]
    try:
        resp = requests.post(
            "https://api.razorpay.com/v1/orders",
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            json={"amount": price * 100, "currency": "INR", "receipt": f"order_{plan}_{current_user()['id']}"},
            timeout=10
        )
        order = resp.json()
        return jsonify({"order_id": order["id"], "amount": order["amount"], "currency": order["currency"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/payment/verify", methods=["POST"])
@login_required
def verify_payment():
    if not RAZORPAY_KEY_SECRET:
        return jsonify({"error": "Payment not configured"}), 503
    data = request.get_json() or {}
    order_id   = data.get("razorpay_order_id", "")
    payment_id = data.get("razorpay_payment_id", "")
    signature  = data.get("razorpay_signature", "")
    plan       = data.get("plan", "pro")
    body       = f"{order_id}|{payment_id}"
    expected   = hmac.new(RAZORPAY_KEY_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return jsonify({"error": "Payment verification failed"}), 400
    u  = current_user()
    db = get_db()
    db.execute("UPDATE users SET plan=? WHERE id=?", (plan, u["id"]))
    db.commit()
    session["plan"] = plan
    return jsonify({"ok": True, "plan": plan})


# ── Referral ────────────────────────────────────────────────────────────────
@app.route("/api/referral/code")
@login_required
def get_referral_code():
    u = current_user()
    return jsonify({"code": u["referral_code"] or ""})

@app.route("/api/referral/apply", methods=["POST"])
@login_required
def apply_referral():
    u    = current_user()
    data = request.get_json() or {}
    code = data.get("code", "").strip().upper()
    if not code:
        return jsonify({"error": "Code required"}), 400
    db = get_db()
    if u["referred_by"]:
        return jsonify({"error": "You have already used a referral code"}), 400
    referrer = db.execute("SELECT * FROM users WHERE referral_code=?", (code,)).fetchone()
    if not referrer or referrer["id"] == u["id"]:
        return jsonify({"error": "Invalid referral code"}), 400
    db.execute("UPDATE users SET referred_by=?, bonus_credits=bonus_credits+10 WHERE id=?",
               (code, u["id"]))
    db.execute("UPDATE users SET bonus_credits=bonus_credits+10 WHERE id=?",
               (referrer["id"],))
    db.commit()
    return jsonify({"ok": True, "bonus": 10})


# ── API Key management ───────────────────────────────────────────────────────
@app.route("/api/key/generate", methods=["POST"])
@login_required
def generate_api_key_route():
    u    = current_user()
    if u["plan"] not in ("pro", "business"):
        return jsonify({"error": "API keys require Pro or Business plan"}), 403
    db   = get_db()
    key  = make_api_key()
    db.execute("UPDATE users SET api_key=? WHERE id=?", (key, u["id"]))
    db.commit()
    return jsonify({"api_key": key})


# ── Admin actions ────────────────────────────────────────────────────────────
@app.route("/api/admin/set-plan", methods=["POST"])
@admin_required
def admin_set_plan():
    data = request.get_json() or {}
    uid  = data.get("user_id")
    plan = data.get("plan", "free")
    if plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400
    db   = get_db()
    db.execute("UPDATE users SET plan=? WHERE id=?", (plan, uid))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/admin/toggle-admin", methods=["POST"])
@admin_required
def admin_toggle_admin():
    data = request.get_json() or {}
    uid  = data.get("user_id")
    db   = get_db()
    user = db.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        return jsonify({"error": "User not found"}), 404
    db.execute("UPDATE users SET is_admin=? WHERE id=?", (0 if user["is_admin"] else 1, uid))
    db.commit()
    return jsonify({"ok": True})


# ── SEO ──────────────────────────────────────────────────────────────────────
@app.route("/sitemap.xml")
def sitemap():
    domain = os.environ.get("REPLIT_DOMAINS", "localhost").split(",")[0]
    pages  = ["", "studio", "pricing", "about", "blog", "contact", "terms", "privacy"]
    urls   = "\n".join(
        f"  <url><loc>https://{domain}/{p}</loc></url>" for p in pages
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>"""
    return Response(xml, mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    domain = os.environ.get("REPLIT_DOMAINS", "localhost").split(",")[0]
    txt = f"User-agent: *\nAllow: /\nSitemap: https://{domain}/sitemap.xml\n"
    return Response(txt, mimetype="text/plain")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
