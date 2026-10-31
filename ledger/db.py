"""File-backed SQLite connections, safe transactions and versioned initialization."""
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from ledger.errors import (
    LedgerBusy, LedgerConflict, LedgerReferenceError, LedgerStorageError,
)

SCHEMA_VERSION = 1


@asynccontextmanager
async def connection(path: str | Path, *, create: bool = False, timeout: float = 5):
    path = Path(path).resolve()
    try:
        target = str(path) if create else path.as_uri() + "?mode=rw"
        async with aiosqlite.connect(target, uri=not create, timeout=timeout,
                                     isolation_level=None) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("PRAGMA recursive_triggers = ON")
            yield db
    except aiosqlite.IntegrityError as error:
        if "FOREIGN KEY" in str(error):
            raise LedgerReferenceError("Referenced record does not exist in this run.") from None
        raise LedgerConflict("Duplicate or immutable record, or invalid database constraint.") from None
    except aiosqlite.OperationalError as error:
        if "locked" in str(error) or "busy" in str(error):
            raise LedgerBusy("Database is busy; retry the operation after the writer finishes.") from None
        raise LedgerStorageError("Cannot access ledger; initialize it and check file permissions.") from None
    except aiosqlite.DatabaseError:
        raise LedgerStorageError("Ledger database operation failed.") from None


@asynccontextmanager
async def transaction(db):
    await db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        await db.commit()
    except BaseException:
        await db.rollback()
        raise


async def initialize(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with connection(path, create=True) as db:
        version = (await db.execute_fetchall("PRAGMA user_version"))[0][0]
        if version > SCHEMA_VERSION:
            raise LedgerStorageError("Ledger schema is newer than this application supports.")
        await db.execute("PRAGMA journal_mode = WAL")
        schema = Path(__file__).with_name("models.sql").read_text()
        try:
            # Version 0 is the supplied skeleton; v1 adds metadata and indexes.
            await db.executescript("BEGIN IMMEDIATE;\n" + schema
                                   + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;")
        except BaseException:
            await db.rollback()
            raise
