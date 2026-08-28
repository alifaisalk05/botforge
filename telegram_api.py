import requests

BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    pass


def _call(token, method, payload=None):
    url = BASE.format(token=token, method=method)
    try:
        resp = requests.post(url, data=payload or {}, timeout=15)
        data = resp.json()
    except Exception as e:
        raise TelegramError(f"Network error contacting Telegram: {e}")
    if not data.get("ok"):
        raise TelegramError(data.get("description", "Unknown Telegram API error"))
    return data.get("result")


def get_me(token):
    return _call(token, "getMe")


def set_webhook(token, url):
    return _call(token, "setWebhook", {"url": url, "allowed_updates": '["message","callback_query"]'})


def delete_webhook(token):
    return _call(token, "deleteWebhook")
