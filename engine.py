"""
engine.py — The HiveScript execution engine.

Every command a user writes is a small Python-flavored script that runs
inside a restricted sandbox with a handful of pre-bound objects:

    bot / Bot   -> send messages, photos, buttons, broadcast, schedule
    message/msg -> the incoming Telegram message
    params      -> text typed after the command, e.g. "/start ref_882" -> "ref_882"
    User        -> per-user persistent key/value storage for THIS bot
    Account     -> info about the bot itself

This is intentionally close in spirit to real-world "low-code bot" scripting
languages: a constrained Python subset, not a full interpreter escape hatch.
"""
import json
import math
import random
import re
import string
import time
import requests

from db import get_db, now

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

SAFE_BUILTINS = {
    "len": len, "str": str, "int": int, "float": float, "bool": bool,
    "list": list, "dict": dict, "set": set, "tuple": tuple,
    "range": range, "enumerate": enumerate, "zip": zip, "map": map,
    "filter": filter, "sorted": sorted, "sum": sum, "min": min, "max": max,
    "abs": abs, "round": round, "print": print, "isinstance": isinstance,
    "type": type, "reversed": reversed, "any": any, "all": all,
    "True": True, "False": False, "None": None,
}

SAFE_MODULES = {
    "math": math, "random": random, "string": string, "json": json,
    "time": time, "re": re,
}


class ScriptError(Exception):
    pass


def _tg_call(token, method, payload=None, files=None):
    url = TELEGRAM_API.format(token=token, method=method)
    resp = requests.post(url, data=payload or {}, files=files, timeout=15)
    data = resp.json()
    if not data.get("ok"):
        raise ScriptError(f"Telegram API error on {method}: {data.get('description')}")
    return data.get("result")


class BotAPI:
    """Exposed to scripts as `bot` and `Bot`."""

    def __init__(self, bot_row, chat_id, engine):
        self._bot = bot_row
        self._chat_id = chat_id
        self._engine = engine

    def sendMessage(self, text, buttons=None, parse_mode="HTML", **kwargs):
        payload = {"chat_id": self._chat_id, "text": str(text), "parse_mode": parse_mode}
        if buttons:
            payload["reply_markup"] = json.dumps(self._build_keyboard(buttons))
        return _tg_call(self._bot["token"], "sendMessage", payload)

    def sendPhoto(self, url, caption=None, buttons=None, **kwargs):
        payload = {"chat_id": self._chat_id, "photo": url}
        if caption:
            payload["caption"] = caption
        if buttons:
            payload["reply_markup"] = json.dumps(self._build_keyboard(buttons))
        return _tg_call(self._bot["token"], "sendPhoto", payload)

    def sendDocument(self, url, caption=None, **kwargs):
        payload = {"chat_id": self._chat_id, "document": url}
        if caption:
            payload["caption"] = caption
        return _tg_call(self._bot["token"], "sendDocument", payload)

    def sendChatAction(self, action="typing"):
        return _tg_call(self._bot["token"], "sendChatAction", {"chat_id": self._chat_id, "action": action})

    def broadcast(self, text, buttons=None):
        """Send `text` to every known user of this bot. Returns count sent."""
        sent = 0
        with get_db() as conn:
            rows = conn.execute("SELECT tg_user_id FROM bot_users WHERE bot_id=?", (self._bot["id"],)).fetchall()
        for row in rows:
            try:
                payload = {"chat_id": row["tg_user_id"], "text": str(text), "parse_mode": "HTML"}
                if buttons:
                    payload["reply_markup"] = json.dumps(self._build_keyboard(buttons))
                _tg_call(self._bot["token"], "sendMessage", payload)
                sent += 1
            except Exception:
                continue
        return sent

    def runCommandAfter(self, seconds, trigger_name, for_current_user=True):
        """Schedule `trigger_name` to run again after `seconds`."""
        with get_db() as conn:
            conn.execute(
                "INSERT INTO scheduled_tasks (bot_id, tg_user_id, trigger_name, run_at, created_at) VALUES (?,?,?,?,?)",
                (self._bot["id"], str(self._chat_id) if for_current_user else None,
                 trigger_name, now() + float(seconds), now())
            )
        return True

    def handleNextCommand(self, trigger_name):
        """Route the user's NEXT message (regardless of content) to `trigger_name`."""
        with get_db() as conn:
            conn.execute(
                "UPDATE bot_users SET waiting_command=? WHERE bot_id=? AND tg_user_id=?",
                (trigger_name, self._bot["id"], str(self._chat_id))
            )
        return True

    @staticmethod
    def _build_keyboard(buttons):
        """buttons: list of rows, each row a list of (text, callback_or_url) tuples/dicts/strings."""
        keyboard = []
        for row in buttons:
            if not isinstance(row, list):
                row = [row]
            out_row = []
            for btn in row:
                if isinstance(btn, dict):
                    out_row.append(btn)
                elif isinstance(btn, (list, tuple)) and len(btn) == 2:
                    text, target = btn
                    if str(target).startswith("http"):
                        out_row.append({"text": text, "url": target})
                    else:
                        out_row.append({"text": text, "callback_data": target})
                else:
                    out_row.append({"text": str(btn), "callback_data": str(btn)})
            keyboard.append(out_row)
        return {"inline_keyboard": keyboard}


class UserStore:
    """Exposed to scripts as `User` — persistent per-Telegram-user key/value store, scoped to this bot."""

    def __init__(self, bot_id, tg_user_id):
        self.bot_id = bot_id
        self.tg_user_id = str(tg_user_id)
        self.id = self.tg_user_id

    def saveData(self, key, value):
        v = json.dumps(value)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO user_data (bot_id, tg_user_id, key, value) VALUES (?,?,?,?) "
                "ON CONFLICT(bot_id, tg_user_id, key) DO UPDATE SET value=excluded.value",
                (self.bot_id, self.tg_user_id, key, v)
            )
        return True

    def getData(self, key, default=None):
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM user_data WHERE bot_id=? AND tg_user_id=? AND key=?",
                (self.bot_id, self.tg_user_id, key)
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def deleteData(self, key):
        with get_db() as conn:
            conn.execute("DELETE FROM user_data WHERE bot_id=? AND tg_user_id=? AND key=?",
                         (self.bot_id, self.tg_user_id, key))
        return True

    def allData(self):
        with get_db() as conn:
            rows = conn.execute("SELECT key, value FROM user_data WHERE bot_id=? AND tg_user_id=?",
                                 (self.bot_id, self.tg_user_id)).fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                out[r["key"]] = r["value"]
        return out


class MessageProxy:
    """Exposed as `message` / `msg`."""

    def __init__(self, update, chat_id, text, from_user):
        self.raw = update
        self.text = text or ""
        self.chat = type("Chat", (), {"id": chat_id})()
        self.from_user = type("From", (), {
            "id": from_user.get("id"),
            "first_name": from_user.get("first_name", ""),
            "last_name": from_user.get("last_name", ""),
            "username": from_user.get("username", ""),
        })()


class AccountProxy:
    """Exposed as `Account` — info about the bot / its owner."""

    def __init__(self, bot_row, owner_row):
        self.bot_id = bot_row["id"]
        self.bot_name = bot_row["name"]
        self.bot_username = bot_row["username"]
        self.owner_name = owner_row["name"]
        self.owner_email = owner_row["email"]


def run_command(bot_row, owner_row, command_row, update, chat_id, text, params, from_user):
    """Execute a command's HiveScript code in a restricted sandbox.
    Returns (success: bool, error_message: str|None)."""

    msg_obj = MessageProxy(update, chat_id, text, from_user)
    bot_obj = BotAPI(bot_row, chat_id, engine=None)
    user_store = UserStore(bot_row["id"], chat_id)
    account_obj = AccountProxy(bot_row, owner_row)

    sandbox_globals = {"__builtins__": SAFE_BUILTINS}
    sandbox_globals.update(SAFE_MODULES)
    sandbox_locals = {
        "bot": bot_obj,
        "Bot": bot_obj,
        "message": msg_obj,
        "msg": msg_obj,
        "params": params,
        "User": user_store,
        "Account": account_obj,
        "options": {},
        "u": user_store,
    }

    code = command_row["code"]
    try:
        compiled = compile(code, filename=f"<command:{command_row['trigger_name']}>", mode="exec")
        exec(compiled, sandbox_globals, sandbox_locals)
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
