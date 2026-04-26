from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import IngestError, IngestService, ingest_job_payload, scan_payload
from ..services import persist_scans
from ..schemas import IngestImportResponse

try:  # pragma: no cover - exercised implicitly when FastAPI is installed
    from fastapi import APIRouter, Depends, HTTPException, status
except Exception:  # pragma: no cover - fallback for this environment

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _Status:
        HTTP_400_BAD_REQUEST = 400
        HTTP_404_NOT_FOUND = 404

    status = _Status()

    class Depends:  # type: ignore[no-redef]
        def __init__(self, dependency: Any | None = None) -> None:
            self.dependency = dependency

    class APIRouter:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.routes: list[dict[str, Any]] = []

        def _register(self, method: str, path: str, **kwargs: Any):
            def decorator(func):
                self.routes.append({"method": method, "path": path, "handler": func, **kwargs})
                return func

            return decorator

        def get(self, path: str, **kwargs: Any):
            return self._register("GET", path, **kwargs)

        def post(self, path: str, **kwargs: Any):
            return self._register("POST", path, **kwargs)


class IngestPreviewRequest(BaseModel):
    paths: list[str] = Field(default_factory=list, min_length=1)
    recursive: bool = True


class IngestSubmitRequest(BaseModel):
    paths: list[str] = Field(default_factory=list, min_length=1)
    recursive: bool = True
    run_immediately: bool = False


class IngestImportRequest(BaseModel):
    paths: list[str] = Field(default_factory=list, min_length=1)
    recursive: bool = True


_DEFAULT_INGEST_SERVICE = IngestService()


def get_ingest_service() -> IngestService:
    return _DEFAULT_INGEST_SERVICE


def create_ingest_router() -> APIRouter:
    router = APIRouter(prefix="/ingest", tags=["ingest"])

    @router.post("/preview")
    def preview_ingest(
        payload: IngestPreviewRequest,
        service: IngestService = Depends(get_ingest_service),
    ) -> dict[str, Any]:
        try:
            scans = service.preview(payload.paths, recursive=payload.recursive)
        except IngestError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"paths": list(payload.paths), "recursive": payload.recursive, "scans": [scan_payload(scan) for scan in scans]}

    @router.post("/import", response_model=IngestImportResponse)
    def import_into_library(
        payload: IngestImportRequest,
        service: IngestService = Depends(get_ingest_service),
        db: Session = Depends(get_db),
    ) -> IngestImportResponse:
        try:
            scans = service.preview(payload.paths, recursive=payload.recursive)
            result = persist_scans(db, scans)
        except IngestError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return IngestImportResponse(
            imported_paths=list(payload.paths),
            series_created=result.series_created,
            series_updated=result.series_updated,
            issues_created=result.issues_created,
            issues_updated=result.issues_updated,
            archives_created=result.archives_created,
            archives_updated=result.archives_updated,
        )

    @router.post("/jobs")
    def submit_ingest_job(
        payload: IngestSubmitRequest,
        service: IngestService = Depends(get_ingest_service),
    ) -> dict[str, Any]:
        try:
            job = service.submit(payload.paths, recursive=payload.recursive)
            if payload.run_immediately:
                job = service.run(job.job_id)
        except IngestError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return ingest_job_payload(job)

    @router.get("/jobs")
    def list_ingest_jobs(
        service: IngestService = Depends(get_ingest_service),
    ) -> dict[str, Any]:
        return {"jobs": [ingest_job_payload(job) for job in service.list_jobs()]}

    @router.get("/jobs/{job_id}")
    def get_ingest_job(
        job_id: str,
        service: IngestService = Depends(get_ingest_service),
    ) -> dict[str, Any]:
        job = service.get(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown ingest job: {job_id}")
        return ingest_job_payload(job)

    @router.post("/jobs/{job_id}/run")
    def run_ingest_job(
        job_id: str,
        service: IngestService = Depends(get_ingest_service),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            job = service.run(job_id)
            persist_scans(db, job.scans)
        except Exception as exc:
            try:
                service.mark_failed(job_id, str(exc))
            except IngestError:
                pass
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return ingest_job_payload(job)

    return router


router = create_ingest_router()
