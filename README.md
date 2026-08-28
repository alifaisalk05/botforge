# HiveBots

A free, multi-user platform for building and hosting Telegram bots — written in pure
Flask + SQLite so it can be deployed almost anywhere with nothing more than Python.

Users register, connect a bot with a token from **@BotFather**, and write commands in
**HiveScript**, a sandboxed Python-flavored scripting language. HiveBots takes care of
the Telegram webhook, execution, logging, and hosting.

## Features

- Multi-user accounts with a monthly free execution-points allowance
- Multi-bot support per account (unlimited bots)
- Command editor (CodeMirror) with a live variable/snippet reference sidebar
- Sandboxed HiveScript execution engine (`bot`, `message`, `User`, `params`, `Account`)
- Automatic Telegram webhook registration on Start / teardown on Stop
- Per-user persistent key/value storage (`User.saveData` / `User.getData`)
- `handleNextCommand` for multi-step conversations
- `runCommandAfter` scheduled tasks (background thread, no external queue needed)
- One-click and in-code broadcasting to every known user of a bot
- Live error/activity logs per bot
- Bot cloning, token rotation, rename, delete
- In-app notifications
- Full documentation page
- Original, hand-built dark UI with scroll reveals, hero animation, and micro-interactions
  (no React/build step required — plain CSS + vanilla JS)

## Local setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The app runs at `http://localhost:5000`. A SQLite database is created automatically at
`instance/hivebots.db` on first run — nothing else to configure.

### Telegram needs a public HTTPS URL

Telegram webhooks must point at a publicly reachable HTTPS address, so `localhost` won't
receive updates. For local development, tunnel your server with something like:

```bash
ngrok http 5000
```

Then use the `https://xxxx.ngrok.app` URL as your site's public address (the app builds
the webhook URL from the incoming request automatically, so as long as you *access the
dashboard itself* through the tunnel URL when you click "Start bot", the correct webhook
gets registered).

## Deploying

This app has three dependencies (`flask`, `requests`, `gunicorn`) and one file-based
database, so it runs on essentially any host that supports Python:

- **Render / Railway / Fly.io / Heroku** — push the repo, set `SECRET_KEY`, done. The
  included `Procfile` (`gunicorn app:app`) is picked up automatically.
- **A VPS** — `pip install -r requirements.txt`, run behind `gunicorn` + `nginx`, put a
  process manager (systemd, supervisor) in front of it.
- **Docker** — wrap the same two commands in a `Dockerfile`; no build step needed for
  the frontend since there's no JS bundler.

Before going to production:

1. Set a real `SECRET_KEY` environment variable (see `.env.example`).
2. Set `DEBUG=0`.
3. Put the app behind HTTPS (required by Telegram for webhooks).
4. Consider moving `instance/hivebots.db` onto a persistent volume if your host's
   filesystem is ephemeral (e.g. some container platforms) — or swap the `db.py` layer
   for Postgres if you outgrow SQLite.

## Project layout

```
app.py              Routes: auth, dashboard, bot & command CRUD, webhook receiver
engine.py            The HiveScript sandbox that executes command code
telegram_api.py       Thin wrapper around Telegram's Bot API
db.py                 SQLite schema + connection helper (no ORM)
templates/            Jinja2 templates (marketing site + app dashboard)
static/css/style.css  Design system (dark theme, motion, components)
static/js/main.js     Scroll reveals, toasts, code editor bootstrap, etc.
```

## Security note

The HiveScript sandbox restricts built-ins and blocks filesystem/network access from
user code, which is enough to stop accidental damage from your own bots. It is **not**
a hardened security boundary — if you plan to let untrusted third parties write and run
commands on a public deployment, add a stronger isolation layer (subprocess sandboxing,
resource limits, or containers per execution) before relying on it.

## License

Built as a custom project for you — do whatever you like with it.
