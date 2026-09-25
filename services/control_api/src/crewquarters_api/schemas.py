"""HTTP request/response models. JSON is camelCase over HTTP (PLAN.md section 4.3).

The OpenAPI document generated from these models is committed to
``packages/contracts/openapi.yaml`` and checked for drift in CI.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class ErrorDetail(ApiModel):
    code: str = Field(examples=["MODEL_CAPACITY_EXCEEDED"])
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ApiModel):
    error: ErrorDetail


class Page(ApiModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = Field(
        None, description="Opaque cursor for the next page; null on the last page."
    )


# --- Auth -------------------------------------------------------------------------


class UserOut(ApiModel):
    id: uuid.UUID
    username: str
    email: str | None
    role: Literal["owner", "member"]
    created_at: datetime


class BootstrapIn(ApiModel):
    token: str = Field(min_length=16, max_length=256, description="One-time bootstrap token.")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=12, max_length=1024)
    email: str | None = Field(None, max_length=254)


class LoginIn(ApiModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class SessionOut(ApiModel):
    user: UserOut
    csrf_token: str = Field(
        description="Send as the X-CSRF-Token header on state-changing requests."
    )
    expires_at: datetime
    idle_expires_at: datetime


# --- Catalog ----------------------------------------------------------------------


class AgentVersionOut(ApiModel):
    id: uuid.UUID
    version: str
    image: str
    image_digest: str
    sdk_protocol: str
    architectures: list[str]
    triggers: list[str]
    permissions: dict[str, Any]
    resources: dict[str, Any]
    configuration_schema: dict[str, Any]
    result_schema: dict[str, Any] | None
    compatible: bool
    compatibility_issues: list[str]
    created_at: datetime


class CatalogAgentOut(ApiModel):
    agent_id: str
    name: str
    summary: str
    publisher: str
    source: Literal["bundled", "imported"]
    trust_status: Literal["curated", "imported_unreviewed"]
    current_version: str
    versions: list[str]
    latest: AgentVersionOut
    installed: bool


class CatalogImportIn(ApiModel):
    manifest: dict[str, Any] = Field(description="Agent manifest (crewquarters/v1alpha1).")


# --- Installations ----------------------------------------------------------------


class ReadinessCheck(ApiModel):
    name: Literal["enabled", "permissions", "configuration", "architecture", "connection", "model"]
    status: Literal["ok", "missing", "needs_attention"]
    detail: str
    resource: str | None = None


class Readiness(ApiModel):
    ready: bool
    checks: list[ReadinessCheck]


class InstallationCreateIn(ApiModel):
    agent_id: str
    version: str | None = Field(None, description="Defaults to the catalog's current version.")
    config: dict[str, Any] = Field(default_factory=dict)
    approved_permissions: dict[str, Any] = Field(
        description="Must equal the version's requested permissions exactly."
    )
    model_bindings: dict[str, str] = Field(
        default_factory=dict,
        description="Owner choice of variant per requested profile family.",
        examples=[{"local.general": "local.general.quality"}],
    )
    enabled: bool = True


class InstallationPatchIn(ApiModel):
    version: int = Field(description="Current installation version (optimistic concurrency).")
    agent_version: str | None = Field(None, description="Upgrade/downgrade to this agent version.")
    config: dict[str, Any] | None = None
    approved_permissions: dict[str, Any] | None = None
    model_bindings: dict[str, str] | None = None
    enabled: bool | None = None


class InstallationOut(ApiModel):
    id: uuid.UUID
    agent_id: str
    agent_name: str
    agent_version: str
    agent_version_id: uuid.UUID
    config: dict[str, Any]
    requested_permissions: dict[str, Any]
    approved_permissions: dict[str, Any]
    capabilities: list[str]
    model_bindings: dict[str, str]
    needs_reapproval: bool
    enabled: bool
    version: int
    readiness: Readiness
    created_at: datetime
    updated_at: datetime


# --- Runs -------------------------------------------------------------------------

RunStateLiteral = Literal[
    "QUEUED",
    "PREPARING",
    "LOADING_MODEL",
    "RUNNING",
    "WAITING_INPUT",
    "CANCELLING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "INTERRUPTED",
]


class RunCreateIn(ApiModel):
    installation_id: uuid.UUID


class RunOut(ApiModel):
    id: uuid.UUID
    installation_id: uuid.UUID
    agent_id: str
    agent_name: str
    agent_version: str
    trigger: Literal["manual", "schedule"]
    schedule_id: uuid.UUID | None
    scheduled_for: datetime | None
    state: RunStateLiteral
    current_attempt: int
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    retryable: bool
    cancel_requested: bool
    acknowledged_at: datetime | None
    active_seconds_used: float
    input_wait_seconds_used: float
    active_timeout_seconds: int
    max_input_wait_seconds: int
    uses_cloud: bool
    pending_input_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class RunEventOut(ApiModel):
    run_id: uuid.UUID
    sequence: int
    attempt: int
    type: str
    payload: dict[str, Any]
    created_at: datetime
    occurred_at: datetime | None = Field(
        None, description="When the agent emitted the event (agent events only)."
    )


# --- Input requests ---------------------------------------------------------------


class InputRequestOut(ApiModel):
    id: uuid.UUID
    run_id: uuid.UUID
    agent_name: str | None = None
    key: str
    title: str
    prompt: str
    schema_: dict[str, Any] = Field(alias="schema", serialization_alias="schema")
    preview: dict[str, Any] | None
    state: Literal["pending", "answered", "cancelled", "expired"]
    answer: Any | None = None
    deadline: datetime
    version: int
    created_at: datetime
    answered_at: datetime | None


class InputAnswerIn(ApiModel):
    version: int = Field(description="Version of the request the answer is for.")
    value: Any = Field(description="Answer; validated against the request's JSON Schema.")


# --- Schedules --------------------------------------------------------------------


class ScheduleCreateIn(ApiModel):
    installation_id: uuid.UUID
    cron: str = Field(examples=["0 10 * * *"])
    timezone: str = Field(examples=["Asia/Kolkata"], description="IANA timezone name.")
    misfire_policy: Literal["fire_once", "skip"] = "fire_once"
    enabled: bool = True


class SchedulePatchIn(ApiModel):
    version: int
    cron: str | None = None
    timezone: str | None = None
    misfire_policy: Literal["fire_once", "skip"] | None = None
    enabled: bool | None = None


class SchedulePreviewIn(ApiModel):
    cron: str
    timezone: str
    count: int = Field(3, ge=1, le=10)


class OccurrenceOut(ApiModel):
    at: datetime = Field(description="Occurrence instant in UTC.")
    local: str = Field(description="Local wall-clock time with UTC offset, ISO 8601.")
    zone_abbreviation: str


class SchedulePreviewOut(ApiModel):
    cron: str
    timezone: str
    occurrences: list[OccurrenceOut]


class ScheduleOut(ApiModel):
    id: uuid.UUID
    installation_id: uuid.UUID
    agent_name: str
    ready: bool = Field(
        description="Whether the agent's model, connections, and config are ready now."
    )
    blockers: list[ReadinessCheck] = Field(description="Readiness checks that are not ok.")
    cron: str
    timezone: str
    misfire_policy: Literal["fire_once", "skip"]
    enabled: bool
    next_run_at: datetime | None
    next_occurrences: list[OccurrenceOut]
    last_fired_at: datetime | None
    last_run_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


# --- Models and connections (read through owning services) ------------------------


class ModelLeaseOut(ApiModel):
    id: str
    holder_type: Literal["run", "chat", "manual"]
    holder_id: str
    label: str = Field(
        description="Friendly holder name, e.g. 'Chat: Contracts' or 'Run 01a0d4c2'."
    )
    expires_at: datetime


class ModelDownloadOut(ApiModel):
    bytes_done: int = 0
    bytes_total: int | None = None
    current_file: str | None = None
    revision: str | None = None


class ModelOut(ApiModel):
    """Installed-on-disk and loaded-in-memory are separate fields (PLAN.md section 13.8)."""

    id: str
    display_name: str
    family: str
    backend: str = "vllm"
    download_state: Literal[
        "NOT_INSTALLED", "DOWNLOADING", "INSTALLED", "DOWNLOAD_ERROR", "DELETING"
    ]
    memory_state: Literal["NOT_LOADED", "LOADING", "READY", "DRAINING", "LOAD_ERROR", "ERROR"]
    stage: str | None = Field(None, description="Current load stage, e.g. 'Loading weights'.")
    disk_bytes: int | None = None
    download: ModelDownloadOut = Field(default_factory=ModelDownloadOut)
    expected_memory_bytes: int | None = None
    reserved_bytes: int = 0
    context_limit: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    validation: str | None = None
    license: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    load_started_at: datetime | None = None
    ready_at: datetime | None = None
    idle_unload_at: datetime | None = None
    active_leases: list[ModelLeaseOut] = Field(default_factory=list)


class ModelUnloadIn(ApiModel):
    force: bool = Field(False, description="Unload even while runs or chats hold leases.")


class ModelCancelInstallIn(ApiModel):
    clear: bool = Field(False, description="Also delete partially downloaded files.")


class MemoryOut(ApiModel):
    """Unified-memory breakdown for the top-bar resource popover (PLAN.md section 13.3)."""

    total_bytes: int | None
    available_bytes: int | None
    system_reserve_bytes: int
    max_serving_bytes: int
    safety_margin_bytes: int
    reserved_bytes: int
    models: list[dict[str, Any]]


class ConnectionOut(ApiModel):
    provider: Literal["google", "twilio", "openai", "anthropic"]
    display_name: str
    status: Literal["NOT_CONNECTED", "CONNECTED", "NEEDS_ATTENTION", "DISABLED", "UNKNOWN"] = Field(
        description="UNKNOWN: the capability broker could not be reached; nothing is assumed."
    )
    granted_capabilities: list[str]
    last_checked_at: datetime | None
    account: str | None = Field(None, description="Masked account label, when connected.")
    detail: str | None = None


# --- Connection management (proxied to the capability broker) ---------------------------

E164 = r"^\+[1-9][0-9]{7,14}$"


class GoogleStartIn(ApiModel):
    capabilities: list[Literal["gmail.readonly", "spreadsheets"]] = Field(
        min_length=1, description="Consent is requested separately per capability."
    )


class GoogleStartOut(ApiModel):
    authorization_url: str = Field(
        description="Navigate the browser here. The response also sets the HttpOnly "
        "`cq_oauth_binding` cookie that the callback requires."
    )


class TwilioCredentialsIn(ApiModel):
    account_sid: str = Field(pattern=r"^AC[0-9a-fA-F]{32}$")
    auth_token: str = Field(min_length=16, max_length=128, description="Stored; never returned.")
    from_number: str = Field(pattern=E164)


class TwilioTestCallIn(ApiModel):
    to: str = Field(pattern=E164)
    confirm: bool = Field(description="Must be true: the owner confirmed a live call.")


class TwilioTestCallOut(ApiModel):
    placed: bool
    to: str = Field(description="Masked destination.")
    status: str | None = None


class ProviderProfileCreateIn(ApiModel):
    provider: Literal["openai", "anthropic"]
    display_name: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=8, max_length=512, description="Stored; never returned.")
    allowed_models: list[str] = Field(default_factory=list, max_length=50)
    budgets: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ProviderProfileOut(ApiModel):
    id: uuid.UUID
    provider: Literal["openai", "anthropic"]
    display_name: str
    allowed_models: list[str]
    budgets: dict[str, Any]
    enabled: bool
    status: str
    last_checked_at: datetime | None


class ProviderProfileTestOut(ApiModel):
    status: Literal["CONNECTED", "ERROR"]
    detail: str | None = None
    checked_at: datetime


# --- Knowledge (proxied to the knowledge service) ----------------------------------------


class KnowledgeBaseCreateIn(ApiModel):
    name: str = Field(min_length=1, max_length=100)


class KnowledgeBaseOut(ApiModel):
    id: uuid.UUID
    name: str
    embedding_profile: str
    embedding_dimension: int
    created_at: datetime


class DocumentOut(ApiModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    name: str
    mime: str
    bytes: int
    sha256: str
    state: Literal["PENDING", "PROCESSING", "READY", "FAILED"]
    extracted: dict[str, Any] | None = Field(None, description="Extraction summary.")
    error: dict[str, Any] | None = Field(None, description="{code, message} when FAILED.")
    created_at: datetime
    updated_at: datetime


class KnowledgeFilters(ApiModel):
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class KnowledgeQueryIn(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(8, ge=1, le=50)
    max_context_tokens: int = Field(5000, ge=100, le=20000)
    filters: KnowledgeFilters = Field(default_factory=KnowledgeFilters)


class PassageDocument(ApiModel):
    id: uuid.UUID
    name: str


class PassageOut(ApiModel):
    citation_id: str
    text: str = Field(description="Untrusted document text; render escaped.")
    score: float
    document: PassageDocument
    locator: dict[str, Any]
    location: str


class KnowledgeQueryOut(ApiModel):
    knowledge_base_id: uuid.UUID
    passages: list[PassageOut]


class CitationOut(ApiModel):
    """A citation stored on an assistant message, with the document's current state."""

    index: int
    citation_id: str
    text: str = Field(description="The passage as retrieved; untrusted, render escaped.")
    score: float | None = None
    document: PassageDocument
    locator: dict[str, Any]
    location: str
    knowledge_base_id: uuid.UUID
    document_available: bool = Field(
        description="False once the document was deleted: its chunks no longer appear in chat."
    )
    document_state: str | None = None


# --- Settings, system, audit ------------------------------------------------------


class SettingsOut(ApiModel):
    timezone: str
    idle_unload_seconds: int
    callback_base_url: str = Field(
        description="Read-only: set by CQ_PUBLIC_BASE_URL, the single source the capability "
        "broker uses for the OAuth redirect and Twilio callbacks."
    )
    callback_urls: dict[str, str] = Field(
        description="Exact URLs to register: googleRedirectUri, twilioCallbackBase."
    )
    setup_completed: bool
    setup_state: dict[str, Any] = Field(
        description="Server-side first-run wizard progress (resumes after refresh/OAuth)."
    )
    versions: dict[str, int] = Field(description="Per-setting version for optimistic updates.")


class SettingsPatchIn(ApiModel):
    timezone: str | None = None
    idle_unload_seconds: int | None = Field(None, ge=60, le=86_400)
    callback_base_url: str | None = Field(
        None,
        deprecated=True,
        description="Read-only; set CQ_PUBLIC_BASE_URL instead. Sending it returns 422 "
        "SETTING_READ_ONLY.",
    )
    setup_completed: bool | None = None
    setup_state: dict[str, Any] | None = None
    versions: dict[str, int] = Field(
        default_factory=dict, description="Expected versions; mismatches return 409."
    )


class StatusCheck(ApiModel):
    group: Literal["device", "runtime", "storage", "database", "network", "models"]
    name: str
    status: Literal["passed", "warning", "failed"]
    detail: str
    checked_at: datetime


class SystemStatusOut(ApiModel):
    status: Literal["healthy", "degraded", "offline"]
    profile: str
    version: str
    architecture: str
    checks: list[StatusCheck]
    runtime: dict[str, Any]


class HealthOut(ApiModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, str] = Field(default_factory=dict)


class AuditEventOut(ApiModel):
    id: uuid.UUID
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_at: datetime


# --- Internal (broker, model gateway, fake runtime) -------------------------------


class AttemptIn(ApiModel):
    attempt: int = Field(ge=1)


class AgentEventIn(AttemptIn):
    type: Literal["run.log", "run.progress", "run.metric", "run.artifact"]
    payload: dict[str, Any]


class BatchEvent(ApiModel):
    client_event_id: str = Field(min_length=1, max_length=64)
    type: str = Field(description="run.log, run.progress, run.metric, or run.artifact.")
    occurred_at: datetime | None = None
    payload: dict[str, Any]


class EventBatchIn(AttemptIn):
    events: list[BatchEvent] = Field(max_length=200)


class EventBatchOut(ApiModel):
    accepted: int = Field(description="Events stored by this call (duplicates excluded).")
    duplicates: int = Field(description="clientEventIds already stored for this run.")
    last_sequence: int = Field(description="Highest event sequence of the run.")


class ModelStateIn(AttemptIn):
    loading: bool
    model: str | None = None


class RunResultIn(AttemptIn):
    status: Literal["succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class AskIn(AttemptIn):
    key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)
    schema_: dict[str, Any] = Field(alias="schema")
    timeout_seconds: int = Field(ge=1, le=86_400)
    preview: dict[str, Any] | None = None


class ActionCompleteIn(AttemptIn):
    result: Any = None


class HeartbeatOut(ApiModel):
    state: RunStateLiteral
    cancel_requested: bool


class InternalRunOut(ApiModel):
    id: uuid.UUID
    state: RunStateLiteral
    current_attempt: int
    installation_id: uuid.UUID
    trigger: Literal["manual", "schedule"]
    scheduled_for: datetime | None
    agent_id: str = Field(description="Manifest agent id.")
    agent_version: str = Field(description="Manifest version (semver).")
    agent_version_id: uuid.UUID
    created_at: datetime
    active_timeout_seconds: int = Field(description="Active-time limit per attempt.")
    active_seconds_remaining: float
    max_input_wait_seconds: int
    input_wait_remaining_seconds: float = Field(description="Remaining input-wait budget.")
    cancel_requested: bool
    capability_token_id: str | None = Field(
        description="jti of the current attempt's capability token; reject any other."
    )
    permissions: dict[str, Any]
    model_bindings: dict[str, str]
    config: dict[str, Any]


class ActionClaimIn(AttemptIn):
    claim_token: str | None = Field(
        None,
        min_length=8,
        max_length=128,
        description="Random per claim() call, reused on its retries. A repeat with the same "
        "token from the same attempt returns the original result.",
    )


class ActionOut(ApiModel):
    key: str
    status: Literal["claimed", "completed", "in_doubt"]
    result: Any = None


class AttentionItem(ApiModel):
    kind: Literal["input_request", "failed_run", "schedule_blocked", "model_action"]
    title: str
    detail: str
    run_id: uuid.UUID | None = None
    input_request_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    model_id: str | None = None
    created_at: datetime | None = None


class AttentionOut(ApiModel):
    count: int = Field(description="Activity badge value: items that need the owner.")
    items: list[AttentionItem]


# --- Chat -------------------------------------------------------------------------------


class ChatSessionCreateIn(ApiModel):
    title: str | None = Field(None, max_length=200)
    model_profile: str = Field("local.general.small", description="Local model variant.")
    knowledge_base_id: uuid.UUID | None = Field(
        None, description="One of your knowledge bases; answers cite its passages."
    )
    retrieval_mode: Literal["when_relevant", "only_knowledge"] = Field(
        "when_relevant",
        description="only_knowledge answers only from retrieved passages and says so, without "
        "calling the model, when nothing is found.",
    )


class ChatMessageIn(ApiModel):
    content: str = Field(min_length=1, max_length=20_000)


class ChatMessageOut(ApiModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    status: Literal["streaming", "complete", "stopped", "failed"]
    citations: list[dict[str, Any]]
    model: str | None
    provider: str | None
    usage: dict[str, Any] | None
    created_at: datetime


class ChatSessionOut(ApiModel):
    id: uuid.UUID
    title: str
    model_profile: str
    knowledge_base_id: uuid.UUID | None
    retrieval_mode: str
    enabled: bool
    holds_model_lease: bool
    local: bool = Field(True, description="Chat never leaves the device.")
    version: int
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class ChatSessionDetailOut(ChatSessionOut):
    messages: list[ChatMessageOut]
