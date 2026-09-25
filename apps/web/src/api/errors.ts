/**
 * The control API's error envelope is `{"error": {code, message, requestId, details}}`
 * (PLAN.md section 4.3). Every failed call surfaces as an ApiError so pages can show
 * plain-language remediation first and the stable code for support.
 */
export interface FieldError {
  path: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null = null,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }

  /** Network failure: the request may not have reached the device. */
  get isNetwork(): boolean {
    return this.code === 'NETWORK_ERROR';
  }

  /**
   * A mutation whose response never arrived. Its effect is unknown until the resource
   * is re-read; the browser never repeats it automatically (section 13.16).
   */
  get isOutcomeUnknown(): boolean {
    return this.code === 'OUTCOME_UNKNOWN';
  }

  get fieldErrors(): FieldError[] {
    const errors = this.details.errors;
    if (!Array.isArray(errors)) return [];
    return errors.flatMap((e): FieldError[] => {
      if (typeof e !== 'object' || e === null) return [];
      const r = e as Record<string, unknown>;
      const path = typeof r.path === 'string' ? r.path : '';
      const message = typeof r.message === 'string' ? r.message : 'Invalid value';
      return [{ path, message }];
    });
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/** Plain-language remediation for codes the operator can act on. */
const REMEDIATION: Record<string, string> = {
  NETWORK_ERROR: 'The device did not respond. Check that it is powered on and on the network.',
  OUTCOME_UNKNOWN:
    'The request timed out, so it may or may not have happened. The page re-reads the current state; check it before trying again.',
  SESSION_EXPIRED: 'Your session has expired. Sign in again.',
  UNAUTHENTICATED: 'Sign in to continue.',
  CSRF_FAILED: 'This page is out of date. Reload it and try again.',
  ORIGIN_REJECTED:
    'The request came from an address the device does not trust. Open Crewquarters from its usual address.',
  RATE_LIMITED: 'Too many attempts. Wait a minute and try again.',
  VERSION_CONFLICT: 'Someone else changed this. Reload to see the latest version.',
  INPUT_ALREADY_ANSWERED: 'This request was already answered.',
  PAYLOAD_TOO_LARGE: 'The file or request is too large.',
  MODEL_GATEWAY_UNAVAILABLE: 'Model serving is unavailable. Check System status.',
  RUNTIME_UNAVAILABLE: 'The agent runtime is unavailable. Check System status.',
  BACKUP_IN_PROGRESS: 'A backup is already queued or running. Wait for it to finish; the list updates on its own.',
  BACKUPS_NOT_CONFIGURED:
    'Backups are not configured on this device (CQ_BACKUP_DIR). Run "crewquarters backup create" on the device instead.',
  BACKUP_CONTAINS_MASTER_KEY: 'This backup contains the device master key, so it can only be copied on the device.',
  BACKUP_UNREADABLE: 'The platform cannot read this backup. Copy it from the device instead.',
};

export function remediation(error: ApiError): string {
  return REMEDIATION[error.code] ?? error.message;
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return new ApiError(0, 'OUTCOME_UNKNOWN', 'The request timed out.');
  }
  if (error instanceof TypeError) {
    return new ApiError(0, 'NETWORK_ERROR', 'The device could not be reached.');
  }
  const message = error instanceof Error ? error.message : 'Unexpected error';
  return new ApiError(0, 'CLIENT_ERROR', message);
}

/** Parse an error envelope from a failed response body (already JSON-decoded). */
export function fromEnvelope(status: number, body: unknown): ApiError {
  if (typeof body === 'object' && body !== null && 'error' in body) {
    const e = (body).error;
    if (typeof e === 'object' && e !== null) {
      const r = e as Record<string, unknown>;
      return new ApiError(
        status,
        typeof r.code === 'string' ? r.code : `HTTP_${status}`,
        typeof r.message === 'string' ? r.message : `Request failed (${status})`,
        typeof r.requestId === 'string' ? r.requestId : null,
        typeof r.details === 'object' && r.details !== null
          ? (r.details as Record<string, unknown>)
          : {},
      );
    }
  }
  return new ApiError(status, `HTTP_${status}`, `Request failed (${status})`);
}
