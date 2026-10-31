"""Initialize the database configured by DATABASE_PATH; no live API calls."""

import asyncio

from app.settings import Settings
from ledger.db import initialize
from ledger.errors import LedgerError


def main():
    try:
        asyncio.run(initialize(Settings().database_path))
    except LedgerError as error:
        raise SystemExit(str(error)) from None
    print("SQLite ledger initialized. Existing records were preserved.")


if __name__ == "__main__":
    main()
