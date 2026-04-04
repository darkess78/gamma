from __future__ import annotations

from pathlib import Path
from typing import Any

from connectors.job_application import JobApplicationLocalFilesConnector


class JobApplicationConnectorClient:
    def __init__(self, workspace_root: str | Path):
        self._connector = JobApplicationLocalFilesConnector(workspace_root)

    def health(self) -> dict[str, Any]:
        return self._connector.health()

    def get_profile(self, include_sections: list[str] | None = None, include_sensitive: bool = False) -> dict[str, Any]:
        return self._connector.get_profile(include_sections=include_sections, include_sensitive=include_sensitive)

    def create_job(
        self,
        company: str,
        role_title: str,
        source_url: str = "",
        location: str = "",
        work_model: str = "unknown",
        created_by: str = "gamma",
    ) -> dict[str, Any]:
        return self._connector.create_job(
            company=company,
            role_title=role_title,
            source_url=source_url,
            location=location,
            work_model=work_model,
            created_by=created_by,
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._connector.get_job(job_id=job_id)

    def ingest_posting(self, job_id: str, posting_text: str, source_url: str = "", captured_at: str | None = None) -> dict[str, Any]:
        return self._connector.ingest_posting(job_id=job_id, posting_text=posting_text, source_url=source_url, captured_at=captured_at)

    def parse_posting(self, job_id: str, mode: str = "deterministic_first") -> dict[str, Any]:
        return self._connector.parse_posting(job_id=job_id, mode=mode)

    def map_question(self, question_text: str, job_id: str | None = None) -> dict[str, Any]:
        return self._connector.map_question(question_text=question_text, job_id=job_id)
