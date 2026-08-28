import os
import json
import secrets
import threading
import time
from functools import wraps
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    jsonify, g, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

from db import init_db, get_db, now
import telegram_api
from engine import run_command

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")


@app.template_filter("timestamp_fmt")
def timestamp_fmt(ts):
    try:
        return datetime.utcfromtimestamp(float(ts)).strftime("%b %d, %H:%M UTC")
    except Exception:
        return ""

FREE_MONTHLY_POINTS = 100000
POINTS_PER_EXECUTION = 1

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def current_month_key():
    return datetime.utcnow().strftime("%Y-%m")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def get_current_user():
    if not session.get("user_id"):
        return None
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if row and row["points_reset_month"] != current_month_key():
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET points_remaining=?, points_reset_month=? WHERE id=?",
                (FREE_MONTHLY_POINTS, current_month_key(), row["id"])
            )
        return get_current_user()
    return row


@app.before_request
def load_user():
    g.user = get_current_user()


@app.context_processor
def inject_globals():
    return {"current_user": g.get("user"), "year": datetime.utcnow().year}


def get_owned_bot_or_404(bot_id):
    with get_db() as conn:
        bot = conn.execute("SELECT * FROM bots WHERE id=? AND user_id=?", (bot_id, g.user["id"])).fetchone()
    if not bot:
        abort(404)
    return bot


def add_log(bot_id, level, message, trigger_name=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO logs (bot_id, level, trigger_name, message, created_at) VALUES (?,?,?,?,?)",
            (bot_id, level, trigger_name, message, now())
        )


def add_notification(user_id, message, kind="info"):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO notifications (user_id, message, kind, created_at) VALUES (?,?,?,?)",
            (user_id, message, kind, now())
        )


# ---------------------------------------------------------------------------
# Public / marketing pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if g.user:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/docs")
def docs():
    return render_template("docs.html")


@app.route("/pricing")
def pricing():
    return render_template("pricing.html")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        error = None
        if not name or len(name) < 2:
            error = "Enter your name."
        elif "@" not in email or "." not in email:
            error = "Enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."

        if not error:
            with get_db() as conn:
                existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
                if existing:
                    error = "An account with that email already exists."

        if error:
            flash(error, "error")
            return render_template("register.html", name=name, email=email)

        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, name, password_hash, points_remaining, points_total, points_reset_month, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (email, name, generate_password_hash(password), FREE_MONTHLY_POINTS, FREE_MONTHLY_POINTS,
                 current_month_key(), now())
            )
            user_id = cur.lastrowid

        add_notification(user_id, "Welcome to HiveBots! You've got 100,000 free execution points this month.", "success")
        session["user_id"] = user_id
        flash("Account created. Welcome aboard!", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        flash("Incorrect email or password.", "error")
        return render_template("login.html", email=email)
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as conn:
        bots = conn.execute(
            "SELECT b.*, "
            "(SELECT COUNT(*) FROM commands c WHERE c.bot_id=b.id) AS command_count, "
            "(SELECT COUNT(*) FROM bot_users bu WHERE bu.bot_id=b.id) AS user_count "
            "FROM bots b WHERE b.user_id=? ORDER BY b.created_at DESC",
            (g.user["id"],)
        ).fetchall()
        notifications = conn.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 6",
            (g.user["id"],)
        ).fetchall()
    total_commands = sum(b["command_count"] for b in bots)
    total_users = sum(b["user_count"] for b in bots)
    return render_template(
        "dashboard.html", bots=bots, notifications=notifications,
        total_commands=total_commands, total_users=total_users,
        points_pct=round(100 * g.user["points_remaining"] / max(g.user["points_total"], 1))
    )


@app.route("/bots/new", methods=["GET", "POST"])
@login_required
def new_bot():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        token = request.form.get("token", "").strip()
        if not name or not token:
            flash("Bot name and API token are required.", "error")
            return render_template("new_bot.html", name=name, token=token)
        try:
            info = telegram_api.get_me(token)
        except telegram_api.TelegramError as e:
            flash(f"Couldn't verify that token with Telegram: {e}", "error")
            return render_template("new_bot.html", name=name, token=token)

        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO bots (user_id, name, username, token, secret, is_active, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (g.user["id"], name, info.get("username"), token, secrets.token_urlsafe(24), 0, now())
            )
            bot_id = cur.lastrowid
            # Seed a friendly default /start command so the bot isn't empty.
            conn.execute(
                "INSERT INTO commands (bot_id, trigger_name, code, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (bot_id, "/start",
                 'first_name = message.from_user.first_name\n'
                 'bot.sendMessage(f"Hey {first_name}! 👋 Welcome.\\n\\nThis bot is powered by HiveBots.")',
                 1, now(), now())
            )
        add_notification(g.user["id"], f"Bot \"{name}\" was added. Write your first commands to bring it to life.", "success")
        flash(f"Connected @{info.get('username')} successfully.", "success")
        return redirect(url_for("bot_detail", bot_id=bot_id))

    return render_template("new_bot.html")


# ---------------------------------------------------------------------------
# Bot detail + tabs
# ---------------------------------------------------------------------------

@app.route("/bots/<int:bot_id>")
@login_required
def bot_detail(bot_id):
    return redirect(url_for("bot_commands", bot_id=bot_id))


@app.route("/bots/<int:bot_id>/commands")
@login_required
def bot_commands(bot_id):
    bot = get_owned_bot_or_404(bot_id)
    with get_db() as conn:
        commands = conn.execute(
            "SELECT * FROM commands WHERE bot_id=? ORDER BY created_at ASC", (bot_id,)
        ).fetchall()
    return render_template("bot_commands.html", bot=bot, commands=commands, tab="commands")


@app.route("/bots/<int:bot_id>/commands/new", methods=["GET", "POST"])
@login_required
def new_command(bot_id):
    bot = get_owned_bot_or_404(bot_id)
    if request.method == "POST":
        trigger = request.form.get("trigger_name", "").strip()
        code = request.form.get("code", "")
        if not trigger:
            flash("Give the command a trigger, like /start or *", "error")
            return render_template("command_form.html", bot=bot, mode="new", trigger_name=trigger, code=code)
        if not trigger.startswith("/") and trigger != "*":
            trigger = "/" + trigger
        with get_db() as conn:
            conn.execute(
                "INSERT INTO commands (bot_id, trigger_name, code, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (bot_id, trigger, code, 1, now(), now())
            )
        flash(f"Command {trigger} created.", "success")
        return redirect(url_for("bot_commands", bot_id=bot_id))
    return render_template("command_form.html", bot=bot, mode="new", trigger_name="", code=(
        '# Write HiveScript here — it is a restricted Python dialect.\n'
        'bot.sendMessage("Hello from your new command!")\n'
    ))


@app.route("/bots/<int:bot_id>/commands/<int:command_id>/edit", methods=["GET", "POST"])
@login_required
def edit_command(bot_id, command_id):
    bot = get_owned_bot_or_404(bot_id)
    with get_db() as conn:
        command = conn.execute("SELECT * FROM commands WHERE id=? AND bot_id=?", (command_id, bot_id)).fetchone()
    if not command:
        abort(404)
    if request.method == "POST":
        trigger = request.form.get("trigger_name", "").strip()
        code = request.form.get("code", "")
        is_active = 1 if request.form.get("is_active") == "on" else 0
        if not trigger.startswith("/") and trigger != "*":
            trigger = "/" + trigger
        with get_db() as conn:
            conn.execute(
                "UPDATE commands SET trigger_name=?, code=?, is_active=?, updated_at=? WHERE id=?",
                (trigger, code, is_active, now(), command_id)
            )
        flash("Command saved.", "success")
        return redirect(url_for("bot_commands", bot_id=bot_id))
    return render_template("command_form.html", bot=bot, mode="edit", command=command,
                            trigger_name=command["trigger_name"], code=command["code"])


@app.route("/bots/<int:bot_id>/commands/<int:command_id>/delete", methods=["POST"])
@login_required
def delete_command(bot_id, command_id):
    get_owned_bot_or_404(bot_id)
    with get_db() as conn:
        conn.execute("DELETE FROM commands WHERE id=? AND bot_id=?", (command_id, bot_id))
    flash("Command deleted.", "success")
    return redirect(url_for("bot_commands", bot_id=bot_id))


@app.route("/bots/<int:bot_id>/logs")
@login_required
def bot_logs(bot_id):
    bot = get_owned_bot_or_404(bot_id)
    with get_db() as conn:
        logs = conn.execute(
            "SELECT * FROM logs WHERE bot_id=? ORDER BY created_at DESC LIMIT 200", (bot_id,)
        ).fetchall()
    return render_template("bot_logs.html", bot=bot, logs=logs, tab="logs")


@app.route("/bots/<int:bot_id>/users")
@login_required
def bot_users(bot_id):
    bot = get_owned_bot_or_404(bot_id)
    with get_db() as conn:
        users = conn.execute(
            "SELECT * FROM bot_users WHERE bot_id=? ORDER BY last_seen DESC LIMIT 500", (bot_id,)
        ).fetchall()
    return render_template("bot_users.html", bot=bot, users=users, tab="users")


@app.route("/bots/<int:bot_id>/broadcast", methods=["GET", "POST"])
@login_required
def bot_broadcast(bot_id):
    bot = get_owned_bot_or_404(bot_id)
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if not text:
            flash("Write a message to broadcast.", "error")
            return redirect(url_for("bot_broadcast", bot_id=bot_id))
        with get_db() as conn:
            recipients = conn.execute(
                "SELECT tg_user_id FROM bot_users WHERE bot_id=?", (bot_id,)
            ).fetchall()
        sent = 0
        for r in recipients:
            try:
                telegram_api._call(bot["token"], "sendMessage", {
                    "chat_id": r["tg_user_id"], "text": text, "parse_mode": "HTML"
                })
                sent += 1
            except Exception:
                continue
        add_log(bot_id, "info", f"Broadcast sent to {sent}/{len(recipients)} users.")
        flash(f"Broadcast delivered to {sent} of {len(recipients)} users.", "success")
        return redirect(url_for("bot_broadcast", bot_id=bot_id))
    with get_db() as conn:
        audience = conn.execute("SELECT COUNT(*) c FROM bot_users WHERE bot_id=?", (bot_id,)).fetchone()["c"]
    return render_template("bot_broadcast.html", bot=bot, tab="broadcast", audience=audience)


@app.route("/bots/<int:bot_id>/settings", methods=["GET", "POST"])
@login_required
def bot_settings(bot_id):
    bot = get_owned_bot_or_404(bot_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "rename":
            new_name = request.form.get("name", "").strip()
            if new_name:
                with get_db() as conn:
                    conn.execute("UPDATE bots SET name=? WHERE id=?", (new_name, bot_id))
                flash("Bot renamed.", "success")
        elif action == "rotate_token":
            new_token = request.form.get("token", "").strip()
            try:
                info = telegram_api.get_me(new_token)
            except telegram_api.TelegramError as e:
                flash(f"Couldn't verify new token: {e}", "error")
                return redirect(url_for("bot_settings", bot_id=bot_id))
            with get_db() as conn:
                conn.execute("UPDATE bots SET token=?, username=? WHERE id=?",
                             (new_token, info.get("username"), bot_id))
            flash("Token updated.", "success")
        elif action == "clone":
            with get_db() as conn:
                cur = conn.execute(
                    "INSERT INTO bots (user_id, name, username, token, secret, is_active, created_at) VALUES (?,?,?,?,?,?,?)",
                    (g.user["id"], bot["name"] + " (Clone)", None, bot["token"], secrets.token_urlsafe(24), 0, now())
                )
                new_bot_id = cur.lastrowid
                cmds = conn.execute("SELECT * FROM commands WHERE bot_id=?", (bot_id,)).fetchall()
                for c in cmds:
                    conn.execute(
                        "INSERT INTO commands (bot_id, trigger_name, code, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (new_bot_id, c["trigger_name"], c["code"], c["is_active"], now(), now())
                    )
            flash("Bot cloned. Update its token before starting it.", "success")
            return redirect(url_for("bot_settings", bot_id=new_bot_id))
        elif action == "delete":
            with get_db() as conn:
                conn.execute("DELETE FROM commands WHERE bot_id=?", (bot_id,))
                conn.execute("DELETE FROM logs WHERE bot_id=?", (bot_id,))
                conn.execute("DELETE FROM bot_users WHERE bot_id=?", (bot_id,))
                conn.execute("DELETE FROM user_data WHERE bot_id=?", (bot_id,))
                conn.execute("DELETE FROM bots WHERE id=?", (bot_id,))
            flash("Bot deleted.", "success")
            return redirect(url_for("dashboard"))
        return redirect(url_for("bot_settings", bot_id=bot_id))

    webhook_url = url_for("webhook", bot_id=bot["id"], secret=bot["secret"], _external=True)
    return render_template("bot_settings.html", bot=bot, tab="settings", webhook_url=webhook_url)


@app.route("/bots/<int:bot_id>/toggle", methods=["POST"])
@login_required
def toggle_bot(bot_id):
    bot = get_owned_bot_or_404(bot_id)
    webhook_url = url_for("webhook", bot_id=bot["id"], secret=bot["secret"], _external=True)
    try:
        if bot["is_active"]:
            telegram_api.delete_webhook(bot["token"])
            with get_db() as conn:
                conn.execute("UPDATE bots SET is_active=0 WHERE id=?", (bot_id,))
            add_log(bot_id, "info", "Bot stopped by owner.")
            flash("Bot stopped.", "success")
        else:
            telegram_api.set_webhook(bot["token"], webhook_url)
            with get_db() as conn:
                conn.execute("UPDATE bots SET is_active=1 WHERE id=?", (bot_id,))
            add_log(bot_id, "info", "Bot started and webhook connected.")
            flash("Bot is live!", "success")
    except telegram_api.TelegramError as e:
        flash(f"Telegram rejected that request: {e}", "error")
    return redirect(request.referrer or url_for("bot_settings", bot_id=bot_id))


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@app.route("/notifications/read-all", methods=["POST"])
@login_required
def read_all_notifications():
    with get_db() as conn:
        conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Webhook receiver — this is where Telegram delivers live updates
# ---------------------------------------------------------------------------

@app.route("/webhook/<int:bot_id>/<secret>", methods=["POST"])
def webhook(bot_id, secret):
    with get_db() as conn:
        bot = conn.execute("SELECT * FROM bots WHERE id=? AND secret=?", (bot_id, secret)).fetchone()
    if not bot:
        abort(404)

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message") or update.get("callback_query", {}).get("message")
    if not message:
        return jsonify({"ok": True})

    from_user = update.get("message", {}).get("from") or update.get("callback_query", {}).get("from") or {}
    chat_id = message.get("chat", {}).get("id")
    text = (update.get("message") or {}).get("text", "") or update.get("callback_query", {}).get("data", "") or ""
    tg_user_id = str(from_user.get("id") or chat_id)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM bot_users WHERE bot_id=? AND tg_user_id=?", (bot_id, tg_user_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE bot_users SET last_seen=?, username=?, first_name=? WHERE bot_id=? AND tg_user_id=?",
                (now(), from_user.get("username"), from_user.get("first_name"), bot_id, tg_user_id)
            )
        else:
            conn.execute(
                "INSERT INTO bot_users (bot_id, tg_user_id, username, first_name, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?)",
                (bot_id, tg_user_id, from_user.get("username"), from_user.get("first_name"), now(), now())
            )
        waiting = existing["waiting_command"] if existing else None

    trigger_name = None
    params = ""
    if waiting:
        trigger_name = waiting
        params = text
        with get_db() as conn:
            conn.execute(
                "UPDATE bot_users SET waiting_command=NULL WHERE bot_id=? AND tg_user_id=?",
                (bot_id, tg_user_id)
            )
    elif text.startswith("/"):
        parts = text.strip().split(maxsplit=1)
        trigger_name = parts[0].split("@")[0]
        params = parts[1] if len(parts) > 1 else ""
    else:
        trigger_name = "*"
        params = text

    with get_db() as conn:
        command = conn.execute(
            "SELECT * FROM commands WHERE bot_id=? AND trigger_name=? AND is_active=1",
            (bot_id, trigger_name)
        ).fetchone()
        if not command and trigger_name != "*":
            command = conn.execute(
                "SELECT * FROM commands WHERE bot_id=? AND trigger_name='*' AND is_active=1", (bot_id,)
            ).fetchone()
        owner = conn.execute("SELECT * FROM users WHERE id=?", (bot["user_id"],)).fetchone()

    if not command:
        return jsonify({"ok": True})

    if owner["points_remaining"] <= 0:
        add_log(bot_id, "error", "Execution skipped: monthly execution points exhausted.", trigger_name)
        return jsonify({"ok": True})

    ok, err = run_command(bot, owner, command, update, chat_id, text, params, from_user)

    with get_db() as conn:
        conn.execute("UPDATE commands SET run_count = run_count + 1 WHERE id=?", (command["id"],))
        conn.execute("UPDATE users SET points_remaining = MAX(points_remaining - ?, 0) WHERE id=?",
                     (POINTS_PER_EXECUTION, owner["id"]))

    if ok:
        add_log(bot_id, "info", f"{trigger_name} executed successfully.", trigger_name)
    else:
        add_log(bot_id, "error", err, trigger_name)

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Background scheduler — runs Bot.runCommandAfter() tasks
# ---------------------------------------------------------------------------

def scheduler_loop():
    while True:
        try:
            with get_db() as conn:
                due = conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE run_at <= ? AND done=0 LIMIT 50", (now(),)
                ).fetchall()
                for task in due:
                    conn.execute("UPDATE scheduled_tasks SET done=1 WHERE id=?", (task["id"],))
                    bot = conn.execute("SELECT * FROM bots WHERE id=?", (task["bot_id"],)).fetchone()
                    if not bot or not bot["is_active"]:
                        continue
                    owner = conn.execute("SELECT * FROM users WHERE id=?", (bot["user_id"],)).fetchone()
                    command = conn.execute(
                        "SELECT * FROM commands WHERE bot_id=? AND trigger_name=? AND is_active=1",
                        (bot["id"], task["trigger_name"])
                    ).fetchone()
                    if not command or not owner or owner["points_remaining"] <= 0:
                        continue
                    targets = [task["tg_user_id"]] if task["tg_user_id"] else [
                        r["tg_user_id"] for r in conn.execute(
                            "SELECT tg_user_id FROM bot_users WHERE bot_id=?", (bot["id"],)
                        ).fetchall()
                    ]
                    for chat_id in targets:
                        fake_update = {"message": {"chat": {"id": chat_id}, "text": ""}}
                        run_command(bot, owner, command, fake_update, chat_id, "", "", {"id": chat_id})
                        conn.execute("UPDATE users SET points_remaining = MAX(points_remaining - 1, 0) WHERE id=?",
                                     (owner["id"],))
        except Exception:
            pass
        time.sleep(5)


def start_background_scheduler():
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="This page wandered off-grid."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Something broke on our end."), 500


init_db()
start_background_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG", "1") == "1")
