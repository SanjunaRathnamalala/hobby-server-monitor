"""Settings from environment variables, read once at start."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    data_dir: str
    app_origin: str
    cookie_secure: bool
    google_client_id: str
    google_client_secret: str
    oauth_redirect_uri: str
    bootstrap_admin_email: str


def _required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing environment variable: {name}")
    return value


def load():
    return Settings(
        db_path=os.environ.get("HSM_DB_PATH", "data/hsm.db"),
        data_dir=os.environ.get("HSM_DATA_DIR", "data"),
        app_origin=_required("APP_ORIGIN"),
        cookie_secure=os.environ.get("COOKIE_SECURE", "true").lower() != "false",
        google_client_id=_required("GOOGLE_CLIENT_ID"),
        google_client_secret=_required("GOOGLE_CLIENT_SECRET"),
        oauth_redirect_uri=_required("OAUTH_REDIRECT_URI"),
        bootstrap_admin_email=_required("BOOTSTRAP_ADMIN_EMAIL").lower(),
    )
