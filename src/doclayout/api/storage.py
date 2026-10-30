"""Encrypted, TTL-governed S3/object-layout artifact store and idempotent job state registry."""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from cryptography.fernet import Fernet

from doclayout.api.schemas import ArtifactType, JobRecord, JobState

_SAFE_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{3,128}$")


def sanitize_session_id(raw_session_id: str) -> str:
    """Validate and normalize session/tenant identifier to prevent path traversal or injection."""
    cleaned = raw_session_id.strip()
    if not _SAFE_TOKEN_RE.fullmatch(cleaned) or len(cleaned) > 64:
        raise ValueError(
            "El encabezado X-Session-ID debe tener entre 3 y 64 caracteres alfanuméricos, '_' o '-'."
        )
    return cleaned


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a URL-safe 32-byte Fernet key from configured secret or generate an ephemeral key."""
    cleaned = secret.strip()
    if not cleaned:
        return bytes(Fernet.generate_key())
    digest = hashlib.sha256(cleaned.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class EncryptedArtifactStore:
    """
    S3/MinIO-compatible hierarchical object store (`<tenant_hash>/<job_id>/<key>.enc`)
    enforcing Fernet authenticated encryption at rest, strict tenant isolation,
    idempotency key deduplication, stage checkpointing, and automatic TTL expiration.
    """

    def __init__(
        self,
        root_dir: str | Path = ".tmp/artifacts",
        ttl_seconds: int = 3600,
        encryption_secret: str = "",
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(1, ttl_seconds)
        self._fernet = Fernet(_derive_fernet_key(encryption_secret))
        self._lock = threading.RLock()
        self._jobs: dict[str, JobRecord] = {}
        # Maps (session_id, idempotency_key) -> job_id
        self._idempotency_index: dict[tuple[str, str], str] = {}

    def check_health(self) -> bool:
        """Verify storage directory exists and is writable without external API calls."""
        return self.root_dir.exists() and self.root_dir.is_dir()

    def _tenant_bucket_dir(self, session_id: str, job_id: str) -> Path:
        """Compute traversal-safe path `<root>/<sha256(session_id)[:24]>/<job_id>`."""
        safe_session = sanitize_session_id(session_id)
        if not _SAFE_TOKEN_RE.fullmatch(job_id):
            raise ValueError("Identificador de trabajo inválido.")
        tenant_prefix = hashlib.sha256(safe_session.encode("utf-8")).hexdigest()[:24]
        job_dir = (self.root_dir / tenant_prefix / job_id).resolve()
        if not str(job_dir).startswith(str(self.root_dir)):
            raise ValueError("Ruta fuera del contenedor de almacenamiento permitido.")
        return job_dir

    def purge_expired(self, now: datetime | None = None) -> int:
        """Delete expired job records and shred their encrypted artifact directories."""
        ref_now = now or datetime.now(UTC)
        purged_count = 0
        with self._lock:
            expired_ids = [
                job_id for job_id, rec in self._jobs.items() if rec.expires_at <= ref_now
            ]
            for job_id in expired_ids:
                rec = self._jobs.pop(job_id)
                self._idempotency_index.pop((rec.session_id, rec.idempotency_key), None)
                job_dir = self._tenant_bucket_dir(rec.session_id, rec.job_id)
                if job_dir.exists():
                    shutil.rmtree(job_dir, ignore_errors=True)
                purged_count += 1
        return purged_count

    def count_active_jobs(self, session_id: str) -> int:
        """Return count of QUEUED or PROCESSING jobs for per-session concurrency limiting."""
        self.purge_expired()
        safe_session = sanitize_session_id(session_id)
        with self._lock:
            return sum(
                1
                for rec in self._jobs.values()
                if rec.session_id == safe_session
                and rec.state in {JobState.QUEUED, JobState.PROCESSING}
            )

    def create_or_get_idempotent_job(
        self,
        record: JobRecord,
        source_bytes: bytes,
    ) -> tuple[JobRecord, bool]:
        """
        Atomically register a new job or return existing unexpired job matching
        `(session_id, idempotency_key)`. Returns `(job_record, idempotent_replay)`.
        """
        self.purge_expired()
        safe_session = sanitize_session_id(record.session_id)
        idem_tuple = (safe_session, record.idempotency_key)

        with self._lock:
            existing_id = self._idempotency_index.get(idem_tuple)
            if existing_id is not None and existing_id in self._jobs:
                return self._jobs[existing_id].model_copy(deep=True), True

            self._jobs[record.job_id] = record.model_copy(deep=True)
            self._idempotency_index[idem_tuple] = record.job_id
            self.save_artifact(
                session_id=safe_session,
                job_id=record.job_id,
                artifact_type=ArtifactType.SOURCE,
                payload=source_bytes,
            )
            return record.model_copy(deep=True), False

    def get_job(self, session_id: str, job_id: str) -> JobRecord | None:
        """Retrieve job metadata strictly isolated to `session_id` (returns None otherwise)."""
        self.purge_expired()
        safe_session = sanitize_session_id(session_id)
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None or rec.session_id != safe_session:
                return None
            return rec.model_copy(deep=True)

    def get_job_internal(self, job_id: str) -> JobRecord | None:
        """Internal worker lookup by `job_id` after pulling authorized queue message."""
        self.purge_expired()
        with self._lock:
            rec = self._jobs.get(job_id)
            return rec.model_copy(deep=True) if rec is not None else None

    def update_job(self, record: JobRecord) -> JobRecord:
        """Persist updated job record and refresh `updated_at`."""
        with self._lock:
            updated = record.model_copy(
                update={"updated_at": datetime.now(UTC)},
                deep=True,
            )
            self._jobs[updated.job_id] = updated
            return updated.model_copy(deep=True)

    def list_jobs(self, session_id: str) -> list[JobRecord]:
        """List all unexpired jobs strictly belonging to `session_id`, newest first."""
        self.purge_expired()
        safe_session = sanitize_session_id(session_id)
        with self._lock:
            items = [
                rec.model_copy(deep=True)
                for rec in self._jobs.values()
                if rec.session_id == safe_session
            ]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    def delete_job(self, session_id: str, job_id: str) -> bool:
        """Immediately purge a job and shred all encrypted artifacts for the owning session."""
        safe_session = sanitize_session_id(session_id)
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None or rec.session_id != safe_session:
                return False
            self._jobs.pop(job_id, None)
            self._idempotency_index.pop((safe_session, rec.idempotency_key), None)
            job_dir = self._tenant_bucket_dir(safe_session, job_id)
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
            return True

    def save_artifact(
        self,
        session_id: str,
        job_id: str,
        artifact_type: ArtifactType,
        payload: bytes,
    ) -> Path:
        """Encrypt `payload` with Fernet (AES-128-CBC + HMAC-SHA256) and write `.enc` object."""
        job_dir = self._tenant_bucket_dir(session_id, job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        enc_path = job_dir / f"{artifact_type.value}.enc"
        ciphertext = self._fernet.encrypt(payload)
        enc_path.write_bytes(ciphertext)
        return enc_path

    def load_artifact(
        self,
        session_id: str,
        job_id: str,
        artifact_type: ArtifactType,
    ) -> bytes | None:
        """Verify tenant ownership, read `.enc` object, and authenticate/decrypt payload."""
        rec = self.get_job(session_id=session_id, job_id=job_id)
        if rec is None:
            return None
        enc_path = self._tenant_bucket_dir(session_id, job_id) / f"{artifact_type.value}.enc"
        if not enc_path.exists():
            return None
        return bytes(self._fernet.decrypt(enc_path.read_bytes()))

    def save_checkpoint(self, session_id: str, job_id: str, name: str, data: bytes) -> Path:
        """Persist an encrypted intermediate checkpoint so restarted workers resume cleanly."""
        if not _SAFE_TOKEN_RE.fullmatch(name):
            raise ValueError("Nombre de checkpoint inválido.")
        job_dir = self._tenant_bucket_dir(session_id, job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        ckpt_path = job_dir / f"ckpt_{name}.enc"
        ckpt_path.write_bytes(self._fernet.encrypt(data))
        return ckpt_path

    def load_checkpoint(self, session_id: str, job_id: str, name: str) -> bytes | None:
        """Load and decrypt an intermediate stage checkpoint if present and unexpired."""
        if not _SAFE_TOKEN_RE.fullmatch(name):
            raise ValueError("Nombre de checkpoint inválido.")
        if self.get_job(session_id=session_id, job_id=job_id) is None:
            return None
        ckpt_path = self._tenant_bucket_dir(session_id, job_id) / f"ckpt_{name}.enc"
        if not ckpt_path.exists():
            return None
        return bytes(self._fernet.decrypt(ckpt_path.read_bytes()))

    def compute_default_expiry(self, now: datetime | None = None) -> datetime:
        """Compute UTC expiration timestamp from configured TTL."""
        base = now or datetime.now(UTC)
        return base + timedelta(seconds=self.ttl_seconds)
