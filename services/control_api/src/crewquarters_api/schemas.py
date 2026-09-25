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
    status: Literal["NOT_CONNECTED", "CONNECTED", "NEEDS_ATTENTION", "DISABLED"]
    granted_capabilities: list[str]
    last_checked_at: datetime | None


# --- Settings, system, audit ------------------------------------------------------


class SettingsOut(ApiModel):
    timezone: str
    idle_unload_seconds: int
    callback_base_url: str | None
    setup_completed: bool
    setup_state: dict[str, Any] = Field(
        description="Server-side first-run wizard progress (resumes after refresh/OAuth)."
    )
    versions: dict[str, int] = Field(description="Per-setting version for optimistic updates.")


class SettingsPatchIn(ApiModel):
    timezone: str | None = None
    idle_unload_seconds: int | None = Field(None, ge=60, le=86_400)
    callback_base_url: str | None = Field(None, pattern=r"^https://[^\s/$.?#].[^\s]*$")
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
    cancel_requested: bool
    capability_token_id: str | None = Field(
        description="jti of the current attempt's capability token; reject any other."
    )
    permissions: dict[str, Any]
    model_bindings: dict[str, str]
    config: dict[str, Any]


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
        None, description="Requires the knowledge service (Nikhil Sajan Khaneja, Person 3)."
    )
    retrieval_mode: Literal["when_relevant", "only_knowledge"] = "when_relevant"


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
