"""Copy the no-secret environment template once; never overwrite existing config."""
from pathlib import Path
import os


def main():
    root = Path(__file__).resolve().parents[1]
    try:
        with (root / ".env").open(
            "x", opener=lambda name, flags: os.open(name, flags, 0o600)
        ) as target:
            target.write((root / ".env.example").read_text())
    except FileExistsError:
        print("Existing .env retained.")
    else:
        print("Created .env for stub mode. Configure secrets before the authentication stage.")


if __name__ == "__main__":
    main()
