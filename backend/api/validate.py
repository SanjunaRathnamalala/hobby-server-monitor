"""Checks for values that come from clients. Each failure is a clear 400."""
import re

import falcon

MAX_BYTES = 2 ** 50  # 1 PiB: far above any real server, blocks absurd values
MAX_CORES = 1024
ROLES = ("admin", "user")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _bad(message):
    raise falcon.HTTPBadRequest(description=message)


def json_object(req, allowed):
    """Read the body; it must be a JSON object with only allowed fields."""
    body = req.get_media()
    if not isinstance(body, dict):
        _bad("The body must be a JSON object.")
    unknown = set(body) - allowed
    if unknown:
        _bad("Unknown fields: " + ", ".join(sorted(unknown)))
    return body


def email(value):
    if (not isinstance(value, str) or len(value) > 254
            or not _EMAIL.fullmatch(value.strip())):
        _bad("email must be a valid email address.")
    return value.strip().lower()


def role(value):
    if value not in ROLES:
        _bad("role must be 'admin' or 'user'.")
    return value


def whole_number(value, name, maximum, minimum=0):
    # bool is checked first because True and False count as int in Python
    if (isinstance(value, bool) or not isinstance(value, int)
            or not minimum <= value <= maximum):
        _bad(f"{name} must be a whole number from {minimum} to {maximum}.")
    return value


_CONTAINER_NAME = re.compile(r"[a-z]([a-z0-9-]{0,61}[a-z0-9])?")


def container_name(value):
    """LXD rules, lowercase only: 1-63 chars, starts with a letter,
    no hyphen at the end."""
    if not isinstance(value, str) or not _CONTAINER_NAME.fullmatch(value):
        _bad("name: lowercase letters, digits and hyphens, 1-63 characters, "
             "starting with a letter and not ending with a hyphen.")
    return value


def boolean(value, name):
    if not isinstance(value, bool):
        _bad(f"{name} must be true or false.")
    return value


def text(value, name, max_length):
    if not isinstance(value, str) or len(value) > max_length:
        _bad(f"{name} must be text of at most {max_length} characters.")
    return value
