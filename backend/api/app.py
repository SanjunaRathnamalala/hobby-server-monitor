"""Falcon application. Run with: gunicorn api.app:app"""
import logging

import falcon

from . import config
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


def create_app(settings=None):
    settings = settings or config.load()
    app = falcon.App(middleware=[Database(settings.db_path), Auth(settings)])
    app.add_route("/api/health", Health())
    app.add_route("/api/me", Me())
    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
