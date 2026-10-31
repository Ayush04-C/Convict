"""Collect credentials privately and atomically update only missing .env keys."""
import getpass
from io import StringIO
import os
from pathlib import Path
import secrets
import tempfile

from dotenv import dotenv_values
from pwdlib import PasswordHash

from app.settings import AuthConfigurationError, Settings

ROOT = Path(__file__).resolve().parents[1]


class AuthSetupError(ValueError):
    """Input error whose message contains no submitted values."""


def configure(path: Path, *, input_fn=None, password_fn=None) -> bool:
    """Return False without prompting when auth is already configured.

    Environment-file values are considered here; exported shell variables are
    deliberately not copied into the file. Other .env contents are preserved.
    """
    input_fn = input_fn or input
    password_fn = password_fn or getpass.getpass
    original = path.read_text() if path.exists() else ""
    values = dotenv_values(stream=StringIO(original), interpolate=False)
    username = values.get("DEMO_USER") or input_fn("Demo username [ayush]: ").strip() or "ayush"
    if not username.strip() or len(username) > 100 or any(c in username for c in "\r\n\0"):
        raise AuthSetupError("DEMO_USER must be 1–100 characters on one line.")
    updates = {}
    if not values.get("DEMO_USER"):
        updates["DEMO_USER"] = username
    if not values.get("DEMO_PASS_HASH"):
        password = password_fn("New demo password (hidden, 12–1024 characters): ")
        if not 12 <= len(password) <= 1024:
            raise AuthSetupError("Password must contain 12–1024 characters.")
        if password != password_fn("Confirm demo password (hidden): "):
            raise AuthSetupError("Passwords did not match; .env was not changed.")
        updates["DEMO_PASS_HASH"] = PasswordHash.recommended().hash(password)
    if not values.get("JWT_SECRET"):
        updates["JWT_SECRET"] = secrets.token_urlsafe(48)
    Settings(_env_file=None, demo_user=username,
             demo_pass_hash=updates.get("DEMO_PASS_HASH", values.get("DEMO_PASS_HASH", "")),
             jwt_secret=updates.get("JWT_SECRET", values.get("JWT_SECRET", ""))).require_auth_credentials()
    if not updates:
        return False

    # Keep comments/provider keys and rewrite only keys being initialized. Quoting
    # is required because Argon2 hashes contain dollar signs.
    lines = original.splitlines(keepends=True)
    remaining = dict(updates)
    for i, line in enumerate(lines):
        candidate = line.strip()
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        key = candidate.split("=", 1)[0].strip()
        if key in updates and "=" in candidate:
            value = updates[key].replace("\\", "\\\\").replace("'", "\\'")
            lines[i] = f"{key}='{value}'\n"
            remaining.pop(key, None)
    content = "".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    for key, value in remaining.items():
        value = value.replace("\\", "\\\\").replace("'", "\\'")
        content += f"{key}='{value}'\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            os.chmod(temporary, 0o600)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main():
    try:
        changed = configure(ROOT / ".env")
    except (AuthSetupError, AuthConfigurationError) as error:
        raise SystemExit(str(error)) from None
    except (ValueError, OSError, EOFError, KeyboardInterrupt):
        raise SystemExit("Setup did not complete. Check your input/configuration; existing .env retained.") from None
    print("Authentication configured in .env." if changed else "Existing authentication settings retained.")
    print("Exported environment variables override .env; restart the API after any change.")


if __name__ == "__main__":
    main()
