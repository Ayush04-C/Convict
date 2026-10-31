"""Company data retains Crustdata group names; per-run evidence is explicit."""

from app.schemas import Dossier, Evidence
from crustdata.client import CrustdataClient, CrustdataError, company_id as get_company_id


async def build_dossier(company_id: int, client: CrustdataClient) -> Dossier:
    result = await client.enrich_result(company_id=company_id)
    matches = [
        match["company_data"]
        for item in result.data
        for match in item["matches"]
        if get_company_id(match["company_data"]) == company_id
    ]
    if not matches:
        raise CrustdataError("not_found", 200, "No company dossier matches the requested ID.")
    if len(matches) != 1:
        raise CrustdataError("response", 200, "Company dossier response is ambiguous.")
    from crustdata.cache import json_text

    data = matches[0]
    updated = data.get("updated_at")
    return Dossier(
        company_id=company_id,
        data_json=json_text(data),
        fetched_at=result.fetched_at,
        source_updated_at=updated if isinstance(updated, str) else None,
    )


def dossier_evidence(run_id: str, dossier: Dossier) -> Evidence:
    return Evidence(
        run_id=run_id,
        evidence_id=f"dossier:{dossier.company_id}",
        agent="leo",
        source="crustdata.company.enrich",
        field_path="company_data",
        value_json=dossier.data_json,
        fetched_at=dossier.fetched_at,
        source_updated_at=dossier.source_updated_at,
    )
