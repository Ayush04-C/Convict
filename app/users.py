"""Accounts created from the sign-up page. Stored beside the ledger; passwords only as Argon2id hashes."""
import re
import sqlite3
from contextlib import closing
from pathlib import Path

USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,31}")
MIN_PASSWORD_LENGTH = 10


class UsernameTaken(Exception):
    pass


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
    )
    return connection


def normalize(username: str) -> str:
    return username.strip().lower()


def create_user(path: Path, username: str, password_hash: str) -> None:
    with closing(_connect(path)) as connection:
        try:
            with connection:
                connection.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                                   (username, password_hash))
        except sqlite3.IntegrityError:
            raise UsernameTaken(username) from None


def get_password_hash(path: Path, username: str) -> str | None:
    with closing(_connect(path)) as connection:
        row = connection.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
    return row[0] if row else None
