"""Falcon application. Run with: gunicorn api.app:app"""
import logging

import falcon

from . import auth, config
from .middleware import Auth, Database


class Health:
    auth = "public"

    def on_get(self, req, resp):
        resp.media = {"status": "ok"}


class Me:
    auth = "user"

    def on_get(self, req, resp):
        user = req.context.user
        resp.media = {"id": user["id"], "email": user["email"], "role": user["role"]}

def _json_errors(req, resp, exc):
    """Always answer errors as JSON, whatever the client asks for."""
    resp.content_type = falcon.MEDIA_JSON
    resp.data = exc.to_json()

def create_app(settings=None):
    settings = settings or config.load()
    app = falcon.App(middleware=[Database(settings.db_path), Auth(settings)])
    app.set_error_serializer(_json_errors)
    app.add_route("/api/health", Health())
    app.add_route("/api/me", Me())
    app.add_route("/api/auth/login", auth.Login(settings))
    app.add_route("/api/auth/callback", auth.Callback(settings))
    app.add_route("/api/auth/logout", auth.Logout())
    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
