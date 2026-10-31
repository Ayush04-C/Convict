"""Isolated offline server for browser tests; never reads the developer's .env."""

from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory

from pwdlib import PasswordHash
import uvicorn

from app.main import create_app
from app.settings import Settings
from tests.pipeline_support import dependencies
from ingest.claim_extractor import ProviderUnavailableError
import pymupdf


def main():
    with TemporaryDirectory(prefix="leo-browser-") as directory:
        root = Path(directory)
        settings = Settings(
            _env_file=None,
            mode="stub",
            demo_user="browser-user",
            demo_pass_hash=PasswordHash.recommended().hash("browser-test-password-42"),
            jwt_secret=secrets.token_urlsafe(48),
            database_path=root / "ledger.sqlite",
            upload_directory=root / "uploads",
        )
        runtime = dependencies()
        extract = runtime.extract
        failed_once = False

        async def controlled_extract(deck):
            nonlocal failed_once
            with pymupdf.open(deck.path) as pdf:
                marker = "EXTRACTION_FAILURE_TEST" in pdf[0].get_text()
            if marker and not failed_once:
                failed_once = True
                raise ProviderUnavailableError("Synthetic provider outage; retry this saved PDF.")
            return await extract(deck)

        runtime.extract = controlled_extract
        uvicorn.run(
            create_app(settings, dependencies=runtime),
            host="127.0.0.1",
            port=int(sys.argv[1]),
            log_level="warning",
        )


if __name__ == "__main__":
    main()
