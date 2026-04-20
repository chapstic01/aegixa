"""
Flask app factory for the Aegixa web dashboard.
"""

import os
from flask import Flask, session, redirect, url_for, request, render_template
from web.auth import get_oauth_url, exchange_code, fetch_user
from web.routes.dashboard import dashboard
from web.routes.console import console_bp
from web.routes.api import api
from web.routes.webhooks import webhooks_bp


def create_app(bot) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("BASE_URL", "").startswith("https")
    app.bot = bot

    # Register blueprints
    app.register_blueprint(dashboard)
    app.register_blueprint(console_bp)
    app.register_blueprint(api)
    app.register_blueprint(webhooks_bp)

    # ---------------------------------------------------------------------------
    # Auth routes
    # ---------------------------------------------------------------------------

    auth_bp = __import__("flask", fromlist=["Blueprint"]).Blueprint("auth", __name__)

    @auth_bp.get("/auth/login")
    def login():
        return redirect(get_oauth_url())

    @auth_bp.get("/auth/callback")
    def callback():
        code = request.args.get("code")
        if not code:
            return render_template("login.html", error="OAuth2 flow cancelled.")

        token_data = exchange_code(code)
        if not token_data or "access_token" not in token_data:
            return render_template("login.html", error="Failed to authenticate with Discord.")

        user = fetch_user(token_data["access_token"])
        if not user:
            return render_template("login.html", error="Failed to retrieve user information.")

        session["user"] = user
        session["access_token"] = token_data["access_token"]
        next_url = request.args.get("next") or url_for("dashboard.servers")
        return redirect(next_url)

    @auth_bp.get("/auth/logout")
    def logout():
        session.clear()
        return redirect(url_for("dashboard.index"))

    app.register_blueprint(auth_bp)

    # ---------------------------------------------------------------------------
    # Health check (keeps Railway service alive)
    # ---------------------------------------------------------------------------

    @app.get("/health")
    def health():
        guilds = len(bot.guilds) if bot.is_ready() else 0
        return {"status": "ok", "guilds": guilds, "ready": bot.is_ready()}

    # ---------------------------------------------------------------------------
    # Error pages
    # ---------------------------------------------------------------------------

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("login.html", error="You don't have permission to access that server."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("login.html", error="Page not found."), 404

    return app
