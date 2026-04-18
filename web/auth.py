"""
Discord OAuth2 helpers for the Aegixa web dashboard.
"""

import os
import requests
from flask import session, redirect, url_for, request
from functools import wraps

DISCORD_API = "https://discord.com/api/v10"
OAUTH_URL = "https://discord.com/api/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
SCOPES = "identify guilds"


def get_oauth_url() -> str:
    base_url = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
    redirect_uri = f"{base_url}/auth/callback"
    return (
        f"{OAUTH_URL}"
        f"?client_id={os.getenv('CLIENT_ID')}"
        f"&redirect_uri={requests.utils.quote(redirect_uri)}"
        f"&response_type=code"
        f"&scope={requests.utils.quote(SCOPES)}"
    )


def exchange_code(code: str) -> dict | None:
    base_url = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
    redirect_uri = f"{base_url}/auth/callback"
    data = {
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET"),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    resp = requests.post(TOKEN_URL, data=data, timeout=10)
    if resp.status_code != 200:
        return None
    return resp.json()


def fetch_user(access_token: str) -> dict | None:
    resp = requests.get(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        return None
    return resp.json()


def fetch_guilds(access_token: str) -> list[dict]:
    resp = requests.get(
        f"{DISCORD_API}/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        return []
    return resp.json()


def login_required(f):
    """Flask decorator — redirects to login if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated


def owner_required(f):
    """Flask decorator — only bot owner can access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        user_id = int(session["user"]["id"])
        owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
        if user_id != owner_id:
            from flask import abort
            abort(403)
        return f(*args, **kwargs)
    return decorated
