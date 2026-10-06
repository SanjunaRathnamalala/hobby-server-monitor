"""Request middleware: one database connection per request, and the
central authorization check (deny by default)."""
import logging

import falcon

from . import db, sessions

log = logging.getLogger("api")

LEVELS = ("public", "user", "admin")
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class Database:
    """Open a connection per request. Commit if the request succeeded,
    otherwise roll back, so a failed request leaves no half-done changes."""

    def __init__(self, path):
        self.path = path

    def process_request(self, req, resp):
        req.context.db = db.connect(self.path)

    def process_response(self, req, resp, resource, req_succeeded):
        conn = getattr(req.context, "db", None)
        if conn is None:
            return
        if req_succeeded:
            conn.commit()
        else:
            conn.rollback()
        conn.close()


class Auth:
    """Every route must declare who may use it, for example:
        auth = "admin"
        auth = {"GET": "user", "DELETE": "admin"}
    A route without a declaration is refused."""

    def __init__(self, settings):
        self.settings = settings

    def process_resource(self, req, resp, resource, params):
        if resource is None:
            return  # no route matched: Falcon answers 404 itself

        rule = getattr(resource, "auth", None)
        if isinstance(rule, dict):
            rule = rule.get(req.method)
        if rule not in LEVELS:
            log.error("no auth rule for %s %s, refusing", req.method, req.path)
            raise falcon.HTTPForbidden()

        if req.method in UNSAFE_METHODS:
            if req.get_header("Origin") != self.settings.app_origin:
                raise falcon.HTTPForbidden(description="Request origin not allowed.")

        conn = req.context.db
        user = sessions.lookup(conn, req.cookies.get(sessions.COOKIE))
        req.context.user = user
        if rule == "public":
            return
        if user is None:
            raise falcon.HTTPUnauthorized(description="Please sign in.")
        if rule == "admin" and user["role"] != "admin":
            raise falcon.HTTPForbidden()

        container = params.get("container_id")
        if container is not None and user["role"] != "admin":
            assigned = conn.execute(
                "SELECT 1 FROM assignments WHERE user_id = ? AND container_uuid = ?",
                (user["id"], container),
            ).fetchone()
            if assigned is None:
                raise falcon.HTTPForbidden()
