"""Ayush: assemble one cached company dossier and preserve source dates."""
from app.schemas import Dossier
from crustdata.client import CrustdataClient


async def build_dossier(company_id: int, client: CrustdataClient) -> Dossier:
    raise NotImplementedError("Stage 4: build_dossier")
