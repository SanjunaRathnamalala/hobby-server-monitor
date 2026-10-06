"""Create or upgrade the database. Run with: python -m api.init_db"""
import os

from .db import init_db


def main():
    path = os.environ.get("HSM_DB_PATH", "data/hsm.db")
    version = init_db(path)
    print(f"database ready at {path} (schema version {version})")


if __name__ == "__main__":
    main()
