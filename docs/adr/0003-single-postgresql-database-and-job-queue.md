# 0003. One PostgreSQL database and a PostgreSQL job queue

- Status: Accepted
- Date: 2026-09-24
- Owner: Nikhil Hiro Ghind (Person 1)

## Context

The platform needs durable application state, schedules, background jobs, run events, audit records, and vectors. Each extra datastore (Redis, a separate vector DB, a workflow engine) adds an `arm64` build, a backup path, and a way to fail on the appliance.

## Decision

**One PostgreSQL 16 database with `pgvector`.** Alembic owns migrations (`services/control_api/migrations`). The initial migration enables the `vector` extension. Large binaries (model weights, document bytes) stay on disk; PostgreSQL stores their paths, checksums, and state.

**Job queue on the `jobs` table**, accessed only through `crewquarters_shared/jobs.py`:

- `enqueue` inserts with `ON CONFLICT DO NOTHING` on `dedupe_key`. A partial unique index makes the key unique only among *live* jobs (`state IN ('available', 'claimed')`).
- `claim` runs `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)`. This sets `lease_owner` and `lease_until` and increments `attempts`. The caller commits before running the job.
- `heartbeat` extends the lease, but only for the current owner. `complete` also checks the owner.
- `fail` retries with bounded exponential backoff and jitter: 2 s base, 300 s cap, and a random delay in the upper half of the window. When a failure is permanent or `attempts` reaches `max_attempts`, the job moves to `dead`.
- `reap_expired` returns expired leases to `available`, or moves them to `dead` once attempts are used up.
- Dead jobs create a `job.dead` audit event and settle the run they belong to (`crewquarters_scheduler/worker.py:on_dead_job`).

**Delivery is at least once.** Handlers are idempotent and resume from durable run and attempt state. External side effects use explicit action keys (`ctx.idempotency`).

**Duplicate defenses for scheduled runs:**

1. The job dedupe key is `schedule:{schedule_id}:{scheduled_for}`, and run dispatch uses `run:{id}:attempt:{n}`.
2. The scheduler claims due schedules with `FOR UPDATE SKIP LOCKED`, then creates the run, enqueues its job, and advances `next_run_at` in one transaction.
3. The final defense is a unique partial index on `agent_runs (schedule_id, scheduled_for)`. Inserts use `ON CONFLICT DO NOTHING`.

**Advisory locks:**

- Scheduler/reconciler leadership: `pg_try_advisory_lock(0x43515343)` ("CQSC"), held on a dedicated connection. If that connection is lost, the lock is released.
- Migrations: `pg_advisory_lock(0x43514D47)` ("CQMG") in `migrations/env.py`, so migrations run exactly once.

## Consequences

- There is no Redis. Queue throughput is bounded by PostgreSQL, which is ample for one appliance.
- Tests kill a worker between claim and completion and prove that exactly one run record and one container exist (`services/scheduler/tests/test_scheduler.py`).
- Backups cover state with one `pg_dump` plus the document files.
- Temporal, Celery, and Kafka remain deferred alternatives (PLAN.md section 1).
