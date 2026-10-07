"""Google sign-in (OAuth 2.0 authorization code flow with PKCE) and logout."""
import base64
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode
from . import audit, sessions

import falcon
import requests
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from . import sessions

log = logging.getLogger("api")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
FLOW_COOKIE = "hsm_oauth"
FLOW_PATH = "/api/auth"


def _pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _redirect(resp, url):
    """Redirect without raising an exception, so the request counts as
    successful and database changes (the new session) are committed."""
    resp.status = falcon.HTTP_303
    resp.location = url


class Login:
    auth = "public"

    def __init__(self, settings):
        self.s = settings

    def on_get(self, req, resp):
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        resp.set_cookie(FLOW_COOKIE, f"{state}.{verifier}", max_age=600,
                        path=FLOW_PATH, secure=self.s.cookie_secure,
                        http_only=True, same_site="Lax")
        params = {
            "client_id": self.s.google_client_id,
            "redirect_uri": self.s.oauth_redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        _redirect(resp, f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


class Callback:
    auth = "public"

    def __init__(self, settings):
        self.s = settings

    def on_get(self, req, resp):
        resp.unset_cookie(FLOW_COOKIE, path=FLOW_PATH)
        if req.get_param("error"):
            return _redirect(resp, self._home("cancelled"))

        email = self._verified_email(req)
        if email is None:
            return _redirect(resp, self._home("failed"))

        conn = req.context.db
        user_id = self._find_user(conn, email)
        if user_id is None:
            log.info("refused sign-in for uninvited email %s", email)
            return _redirect(resp, self._home("not_invited"))

        sessions.delete_expired(conn)
        token, _ = sessions.create(conn, user_id)
        resp.set_cookie(sessions.COOKIE, token,
                        max_age=int(sessions.LIFETIME.total_seconds()),
                        path="/", secure=self.s.cookie_secure,
                        http_only=True, same_site="Lax")
        _redirect(resp, self.s.app_origin + "/")

    def _home(self, error):
        return f"{self.s.app_origin}/?login_error={error}"

    def _verified_email(self, req):
        """Check state, exchange the code with Google, verify the ID token.
        Return the verified email address, or None."""
        state, _, verifier = req.cookies.get(FLOW_COOKIE, "").partition(".")
        returned_state = req.get_param("state") or ""
        code = req.get_param("code")
        if not (state and verifier and code) or not secrets.compare_digest(
                state.encode(), returned_state.encode()):
            log.warning("sign-in refused: state missing or not matching")
            return None

        try:
            reply = requests.post(GOOGLE_TOKEN_URL, timeout=10, data={
                "code": code,
                "client_id": self.s.google_client_id,
                "client_secret": self.s.google_client_secret,
                "redirect_uri": self.s.oauth_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            })
            reply.raise_for_status()
            claims = id_token.verify_oauth2_token(
                reply.json()["id_token"], google_requests.Request(),
                self.s.google_client_id)
        except (requests.RequestException, KeyError, ValueError,
                google_exceptions.GoogleAuthError) as exc:
            log.warning("sign-in failed: %s", exc)
            return None

        if not claims.get("email_verified"):
            log.warning("sign-in refused: email not verified by Google")
            return None
        return claims.get("email", "").lower() or None

    def _find_user(self, conn, email):
        """Return the user id for an invited email, or create the first
        admin from BOOTSTRAP_ADMIN_EMAIL. Anyone else gets None."""
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return row["id"]
        if email != self.s.bootstrap_admin_email:
            return None
        if conn.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
            return None  # bootstrap only works while no admin exists

        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO users (email, role, created_at) VALUES (?, 'admin', ?)",
            (email, now))
        audit.record(conn, email, "bootstrap_admin", email)
        log.info("bootstrap admin created: %s", email)
        return cur.lastrowid


class Logout:
    auth = "public"

    def on_post(self, req, resp):
        sessions.delete(req.context.db, req.cookies.get(sessions.COOKIE))
        resp.unset_cookie(sessions.COOKIE, path="/")
        resp.status = falcon.HTTP_204
