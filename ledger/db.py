"""Ayush: schema initialization only; repository transactions follow next."""
from pathlib import Path

import aiosqlite


async def initialize(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(Path(__file__).with_name("models.sql").read_text())
        await db.commit()
