"""Collect credentials privately and atomically update only missing .env keys."""
import argparse
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

# A deliberately fake, publicly documented account for trying Leo on your own machine. It is only
# ever created by `--demo` (`make setup`), only when no password is configured yet, and the server
# listens on 127.0.0.1. Never expose a server that uses it; run this script without --demo to
# choose your own password.
DEMO_USERNAME = "maya"
DEMO_PASSWORD = "maya-yc-demo-2026"


class AuthSetupError(ValueError):
    """Input error whose message contains no submitted values."""


def configure(path: Path, *, input_fn=None, password_fn=None, demo: bool = False) -> bool:
    """Return False without prompting when auth is already configured.

    With demo=True, a missing password is set up as the documented fake demo account with no
    prompts. Credentials that already exist are never changed.

    Environment-file values are considered here; exported shell variables are
    deliberately not copied into the file. Other .env contents are preserved.
    """
    input_fn = input_fn or input
    password_fn = password_fn or getpass.getpass
    original = path.read_text() if path.exists() else ""
    values = dotenv_values(stream=StringIO(original), interpolate=False)
    demo = demo and not values.get("DEMO_PASS_HASH")
    if demo:
        username, password_fn = DEMO_USERNAME, lambda prompt: DEMO_PASSWORD
    else:
        username = values.get("DEMO_USER") or input_fn("Demo username [ayush]: ").strip() or "ayush"
    if not username.strip() or len(username) > 100 or any(c in username for c in "\r\n\0"):
        raise AuthSetupError("DEMO_USER must be 1–100 characters on one line.")
    updates = {}
    if not values.get("DEMO_USER") or demo:
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
    parser = argparse.ArgumentParser(description="Configure the demo login stored in .env.")
    parser.add_argument("--demo", action="store_true",
                        help=f"create the documented fake account ({DEMO_USERNAME}) without prompting")
    demo = parser.parse_known_args()[0].demo   # tolerant: other arguments (such as pytest's) are ignored
    try:
        changed = configure(ROOT / ".env", **({"demo": True} if demo else {}))
    except (AuthSetupError, AuthConfigurationError) as error:
        raise SystemExit(str(error)) from None
    except (ValueError, OSError, EOFError, KeyboardInterrupt):
        raise SystemExit("Setup did not complete. Check your input/configuration; existing .env retained.") from None
    print("Authentication configured in .env." if changed else "Existing authentication settings retained.")
    if demo and changed:
        print(f"Demo login (fake, for local use only): {DEMO_USERNAME} / {DEMO_PASSWORD}")
    print("Exported environment variables override .env; restart the API after any change.")


if __name__ == "__main__":
    main()
