"""Application settings.

Settings are loaded from conservative built-in defaults, optionally overlaid with the server YAML
file selected by ``PARACORD_SERVER_CONFIG``, and finally overridden by environment variables.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_PATH = Path("config/server.local.yaml")


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML settings from ``path`` if it exists."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Server config must be a YAML mapping: {path}")
    return data


def _server_settings_from_yaml(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten supported YAML settings into the runtime settings shape."""
    server = data.get("server") or {}
    security = data.get("security") or {}
    services = data.get("services") or {}

    values: dict[str, Any] = {}
    if "bind_host" in server:
        values["bind_host"] = server["bind_host"]
    if "bind_port" in server:
        values["bind_port"] = server["bind_port"]
    if "allow_lan_access" in server:
        values["lan_mode"] = server["allow_lan_access"]
    if "public_base_url" in server:
        values["public_base_url"] = server["public_base_url"]
    if "allowed_roles" in security:
        values["allowed_roles"] = security["allowed_roles"]
    if "session_ttl_minutes" in security:
        values["session_ttl_minutes"] = security["session_ttl_minutes"]
    if "login_max_failures" in security:
        values["login_max_failures"] = security["login_max_failures"]
    if "login_lockout_minutes" in security:
        values["login_lockout_minutes"] = security["login_lockout_minutes"]
    if "database_url" in services:
        values["database_url"] = services["database_url"]
    if "redis_url" in services:
        values["redis_url"] = services["redis_url"]
    if "grobid_url" in services:
        values["grobid_url"] = services["grobid_url"]
    if "ollama_url" in services:
        values["ollama_url"] = services["ollama_url"]
    grobid = (data.get("processing") or {}).get("grobid") or {}
    if "consolidate_header" in grobid:
        values["grobid_consolidate_header"] = grobid["consolidate_header"]
    if "consolidate_citations" in grobid:
        values["grobid_consolidate_citations"] = grobid["consolidate_citations"]
    if "include_raw_citations" in grobid:
        values["grobid_include_raw_citations"] = grobid["include_raw_citations"]
    if "segment_sentences" in grobid:
        values["grobid_segment_sentences"] = grobid["segment_sentences"]
    if "include_coordinates" in grobid:
        values["grobid_coordinate_elements"] = grobid["include_coordinates"]
    storage = data.get("storage") or {}
    if "managed_library_root" in storage:
        values["managed_library_root"] = storage["managed_library_root"]
    if "server_allowed_roots" in storage:
        values["server_allowed_roots"] = storage["server_allowed_roots"]
    enrichment = data.get("metadata_enrichment") or {}
    if "enabled" in enrichment:
        values["enrichment_enabled"] = enrichment["enabled"]
    sources = enrichment.get("sources") or {}
    if "arxiv" in sources:
        values["enrichment_arxiv"] = sources["arxiv"]
    if "crossref" in sources:
        values["enrichment_crossref"] = sources["crossref"]
    if "openalex" in sources:
        values["enrichment_openalex"] = sources["openalex"]
    if "semantic_scholar" in sources:
        values["enrichment_semantic_scholar"] = sources["semantic_scholar"]
    if "crossref_mailto" in enrichment:
        values["crossref_mailto"] = enrichment["crossref_mailto"]

    # Find-on-web (#5): aggregate candidate matches from legitimate scholarly sources only.
    web_find = data.get("web_find") or {}
    if "enabled" in web_find:
        values["web_find_enabled"] = web_find["enabled"]
    if "unpaywall_email" in web_find:
        values["web_find_unpaywall_email"] = web_find["unpaywall_email"]
    if "max_candidates" in web_find:
        values["web_find_max_candidates"] = web_find["max_candidates"]
    if "per_source_timeout" in web_find:
        values["web_find_per_source_timeout"] = web_find["per_source_timeout"]
    if "total_budget" in web_find:
        values["web_find_total_budget"] = web_find["total_budget"]
    if "download_timeout" in web_find:
        values["web_find_download_timeout"] = web_find["download_timeout"]
    if "max_download_bytes" in web_find:
        values["web_find_max_download_bytes"] = web_find["max_download_bytes"]
    if "resolve_enabled" in web_find:
        values["web_find_resolve_enabled"] = web_find["resolve_enabled"]
    if "resolve_timeout" in web_find:
        values["web_find_resolve_timeout"] = web_find["resolve_timeout"]

    # Reference→library matching (batch 12): tolerant title(+year+author) matcher for bibliography
    # references. Numeric params are boot-fixed here; the fuzzy-as-confirmed runtime toggle is on the
    # AppConfig DB singleton (Phase 3), not YAML.
    reference_matching = data.get("reference_matching") or {}
    if "enabled" in reference_matching:
        values["reference_matching_enabled"] = reference_matching["enabled"]
    if "title_similarity_threshold" in reference_matching:
        values["reference_matching_title_threshold"] = reference_matching[
            "title_similarity_threshold"
        ]
    if "author_overlap_threshold" in reference_matching:
        values["reference_matching_author_threshold"] = reference_matching[
            "author_overlap_threshold"
        ]
    if "require_year_match" in reference_matching:
        values["reference_matching_require_year_match"] = reference_matching["require_year_match"]
    if "identifier_gate" in reference_matching:
        values["reference_matching_identifier_gate"] = reference_matching["identifier_gate"]

    # OCR / advanced extraction (Phase B5). `processing.ocr` toggles the OCRmyPDF pre-step; the
    # `processing.advanced_extraction` block selects an opt-in ML extractor (activate-when-present).
    processing = data.get("processing") or {}
    ocr = processing.get("ocr") or {}
    if "backend" in ocr:
        values["ocr_backend"] = ocr["backend"]
    elif "enable_fallback" in ocr:
        # Backward-compat: the old boolean toggle maps onto the single ocr_backend enum.
        values["ocr_backend"] = "ocrmypdf" if ocr["enable_fallback"] else "none"
    if "timeout_seconds" in ocr:
        values["ocr_timeout_seconds"] = ocr["timeout_seconds"]
    if "language" in ocr:
        values["ocr_language"] = ocr["language"]
    if "skip_if_text_layer_good" in ocr:
        values["ocr_skip_if_text_layer_good"] = ocr["skip_if_text_layer_good"]
    return values


class Settings(BaseSettings):
    """Runtime settings for the PaRacORD backend."""

    model_config = SettingsConfigDict(populate_by_name=True)

    environment: str = Field(default="production", alias="PARACORD_ENV")
    bind_host: str = Field(default="127.0.0.1", alias="PARACORD_BIND_HOST")
    bind_port: int = Field(default=8000, alias="PARACORD_BIND_PORT")
    lan_mode: bool = Field(default=False, alias="PARACORD_LAN_MODE")
    public_base_url: str = Field(
        default="http://127.0.0.1:8000",
        alias="PARACORD_PUBLIC_BASE_URL",
    )
    database_url: str = Field(
        default="postgresql+psycopg://paperracks:paperracks_dev_password@localhost:5432/paperracks",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    # E1 fail-closed switch. Rate limiting + queue-capacity fail OPEN when Redis is unreachable
    # (correct for single-user: a dead Redis must never take the API down). Set this in a
    # LAN/production deployment to instead fail CLOSED — requests that depend on Redis-backed limits
    # are rejected with 503 while Redis is down, and the admin Jobs view shows a red
    # "limits unavailable" status. Default off preserves the single-user behavior.
    production_require_redis: bool = Field(default=False, alias="PARACORD_PRODUCTION_REQUIRE_REDIS")
    grobid_url: str = Field(default="http://localhost:8070", alias="GROBID_URL")
    # GROBID extraction options (driven from the `grobid:` YAML block / env).
    grobid_consolidate_header: bool = True
    grobid_consolidate_citations: bool = True
    grobid_include_raw_citations: bool = True
    grobid_segment_sentences: bool = True
    # TEI elements to request PDF coordinates for (enables reader anchors); empty disables.
    grobid_coordinate_elements: list[str] = ["ref", "biblStruct", "s", "p"]
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_URL")
    # SSRF opt-in for the admin-set ``ollama_url`` (D6): loopback + docker-service names are always
    # allowed; any other host (an FQDN or a LAN/public IP) is refused unless this is explicitly set.
    allow_external_ollama: bool = Field(default=False, alias="ALLOW_EXTERNAL_OLLAMA")
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    # No guest/anonymous access exists by design; the role set is asserted guest-free at startup
    # (see app.core.security.assert_no_guest_roles). There is intentionally no guest_access flag.
    allowed_roles: list[str] = ["owner", "editor", "reader"]
    session_ttl_minutes: int = Field(default=720, alias="PARACORD_SESSION_TTL_MINUTES")
    managed_library_root: str = "./storage/library"
    # Directory for the persisted BM25F+ lexical index (HS4). On Postgres the eager-scored sparse
    # matrix is saved here and memory-mapped read-only by every worker (one shared physical copy);
    # SQLite/test runs build it in-memory and never touch disk. Lives on the persisted library volume.
    search_index_dir: str = Field(
        default="./storage/search_index", alias="PARACORD_SEARCH_INDEX_DIR"
    )
    # Append-only audit-event file sink (D31.1, SPEC §7.6). Every audit event is also written as one
    # JSON line to this JSONL file on the persisted storage volume — a tamper-evident, DB-independent
    # record. Best-effort: a write failure never breaks the request nor drops the DB row, and
    # appends are safe for concurrent writers. An empty value disables the file sink.
    audit_log_path: str = Field(
        default="./storage/audit/audit.jsonl", alias="PARACORD_AUDIT_LOG_PATH"
    )
    # Per-user UI preferences file (YAML). Defaults to the XDG-ish ~/.config path for bare-metal;
    # docker-compose overrides this to /app/storage/preferences.yaml (the persisted library volume),
    # since the container's ~/.config is neither mounted nor persistent.
    preferences_path: str = Field(
        default="~/.config/paracord/preferences.yaml",
        alias="PARACORD_PREFERENCES_PATH",
    )
    server_allowed_roots: list[Any] = []
    enrichment_enabled: bool = True
    enrichment_arxiv: bool = True
    enrichment_crossref: bool = True
    enrichment_openalex: bool = False
    enrichment_semantic_scholar: bool = False
    crossref_mailto: str | None = None
    # Find-on-web (#5). Keyless-by-default: every source is reachable without an API key.
    # LEGITIMATE SOURCES ONLY — there is intentionally no setting that enables a shadow library.
    web_find_enabled: bool = True
    # Unpaywall requires an email param; defaults to crossref_mailto at call time when unset.
    web_find_unpaywall_email: str | None = None
    web_find_max_candidates: int = 10
    web_find_per_source_timeout: float = 8.0
    web_find_total_budget: float = 25.0
    web_find_download_timeout: float = 60.0
    web_find_max_download_bytes: int = 100 * 1024 * 1024  # 100 MB
    # find-on-web v2.1: resolve each RETURNED candidate's View/redirect target to its final host
    # (display-only "platform"). Short, best-effort, concurrent; never downloads a body.
    web_find_resolve_enabled: bool = True
    web_find_resolve_timeout: float = 4.0
    # Batch citation import (Phase J item 5): cap how many raw lines a single batch preview/commit
    # will process (protects the lookup engine's per-line fan-out from an unbounded paste).
    web_find_batch_max_lines: int = 200
    # Score at/above which a lookup-engine batch line is auto-treated as a confident "matched"
    # (its top candidate prefills the draft); below it the line is "title_only".
    web_find_batch_match_threshold: float = 0.6
    # Reference→library matching (batch 12 — "likely local" citations). Tolerant title(+year+author)
    # matcher that links an extracted bibliography reference to a library work it likely IS, so refs
    # for papers already in the library stop showing as "external". Operator-tuned / boot-fixed
    # (Settings is @lru_cache'd); only the fuzzy-as-confirmed *toggle* is runtime-editable, and it
    # lives on the AppConfig DB singleton instead (batch 12 Phase 3).
    reference_matching_enabled: bool = True
    # similarity_pct (0-100) at/above which a candidate work is a title match. The KnowRob dash/colon
    # pair scores 98.0, so 90 comfortably links it while excluding unrelated same-first-word titles.
    reference_matching_title_threshold: float = 90.0
    # Author-overlap ratio (0-1) a candidate must clear when both sides list authors (Phase 4). The
    # gate is skipped when either side has no authors — a signal you can't compute can't disqualify.
    reference_matching_author_threshold: float = 0.5
    # When both reference and candidate carry a year they must be equal; unset on either side skips it.
    reference_matching_require_year_match: bool = True
    # DOI/arXiv is the authoritative gate: identifiers present on BOTH sides must match exactly (else
    # that candidate is disqualified, no fuzzy fallback). Only when identifiers are absent does fuzzy
    # run. Off = ignore identifiers and always go fuzzy (not recommended).
    reference_matching_identifier_gate: bool = True
    # AI provider seams (Stage 6). Defaults keep the dependency-free lexical baselines; the
    # heavier providers are opt-in and degrade gracefully when their lib/daemon is absent.
    embedding_provider: str = "hash_bow"  # hash_bow | sentence_transformers | ollama
    embedding_model: str | None = None  # provider-specific model id (None = provider default)
    summary_llm_enabled: bool = False  # allow summary_type=local_llm via Ollama
    summary_llm_model: str = "qwen3:4b"
    topic_backend: str = "tfidf"  # tfidf | embedding (BERTopic-style, embedding-clustered)
    # H7: use the pgvector `<=>` operator for ANN ranking when on Postgres (a registered real model
    # gets sub-linear HNSW search out of the box). On SQLite / no-pgvector, or when the ANN column is
    # empty, the code transparently falls back to the JSON-array + Python-cosine path — so the
    # dependency-free hash_bow default keeps working. Enabled by default; a no-op off Postgres.
    pgvector_enabled: bool = True
    # OCR / advanced extraction (Phase B5). `ocrmypdf` (default) adds a searchable text layer to
    # scanned/poor-text PDFs before GROBID (bounded local subprocess; no egress). `none` disables
    # the pre-step; `pymupdf` adds a text layer via PyMuPDF + tesseract (no ocrmypdf/ghostscript
    # dependency). GROBID stays the structured TEI extractor either way.
    ocr_backend: str = "ocrmypdf"  # none | ocrmypdf | pymupdf
    ocr_timeout_seconds: int = 300
    ocr_language: str = "eng"
    ocr_skip_if_text_layer_good: bool = True
    # At-rest field encryption key (Fernet). When unset, sensitive fields are stored in clear and
    # SECURITY.md's at-rest claim is downgraded accordingly (Stage 7).
    secret_key: str | None = Field(default=None, alias="PARACORD_SECRET_KEY")
    # Login throttling (Stage 7 auth hardening).
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    # Library pagination (D18). ``default_papers_per_page`` is the fallback page size when a user has
    # no ``papers_per_page`` preference; ``max_papers_per_page`` is the out-of-the-box global clamp
    # (the owner may raise/lower it at runtime via the admin AppConfig row).
    default_papers_per_page: int = 100
    max_papers_per_page: int = 500


def _environment_overrides() -> dict[str, Any]:
    """Return settings values explicitly provided through environment variables."""
    values: dict[str, Any] = {}
    env_settings = Settings()  # type: ignore[call-arg]
    for name, field in Settings.model_fields.items():
        alias = str(field.validation_alias or "")
        if alias and alias in os.environ:
            values[name] = getattr(env_settings, name)
    return values


@lru_cache
def get_settings() -> Settings:
    """Return cached settings."""
    config_path = Path(os.environ.get("PARACORD_SERVER_CONFIG", DEFAULT_CONFIG_PATH))
    yaml_values = _server_settings_from_yaml(_load_yaml(config_path))
    return Settings(**(yaml_values | _environment_overrides()))  # type: ignore[call-arg]
