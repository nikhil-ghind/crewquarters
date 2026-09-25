# Backup, restore, diagnostics and demo reset

PLAN.md §6 (one database), §10.2 (master key), §13.13, §15.2, §23.2, §23.6, §24 and §26 ("a recoverable backup exists before the event").

## What a backup is

`crewquarters-backup-<UTC time>[-<label>].tar.gz`, mode `0600`, holding:

| Part | Contents |
| --- | --- |
| `manifest.json` | format version, platform version, migration head, PostgreSQL version, `createdAt`, SHA-256 and size of every other part, document counts, `includesMasterKey` |
| `database.dump` | `pg_dump --format=custom` of the one `crewquarters` database: settings, users, installations, schedules, runs and events, requests, chat history, audit, knowledge metadata and chunks, connection records with their secrets **still encrypted** |
| `documents.tar` | the uploaded document files that the dumped `documents` rows point at |
| `master.key` | **only** with `--include-master-key` on the device |

A sidecar `<name>.manifest.json` sits next to each archive so the UI can list backups without opening them.

**Consistency.** The backup exports one PostgreSQL snapshot, reads the list of document files in that snapshot, and runs `pg_dump --snapshot` on exactly that snapshot, so the documents in the archive are the ones the dumped rows reference. Uploads in flight (`.staging`) and orphan files are not included. A file purged after the snapshot is counted as `documents.missing` in the manifest. The platform can keep running during a backup.

**Not included:** model files (large and re-downloadable from their pinned revisions; after a restore the gateway re-verifies installed models and Models offers a download for any that are missing), embedding model files, and the master key unless you ask for it.

### The master key (PLAN §10.2)

Connection secrets (Google tokens, Twilio, OpenAI and Anthropic keys) are encrypted with the device master key, `/etc/crewquarters/master.key`. By default a backup does **not** contain it:

- Restoring on the **same device** (same key): connections keep working.
- Restoring on **another device** or after the key was lost: the data comes back, but those secrets cannot be decrypted. The restore does not fail. It marks the affected Google connections `NEEDS_ATTENTION` and the Twilio/OpenAI/Anthropic profiles `ERROR` ("Reconnect in Connections"), and prints which providers to reconnect.
- `crewquarters backup create --include-master-key` adds the key and prints a loud warning. Anyone with that file can use every connected account. Keep it offline and encrypted, and delete it when it is no longer needed. The UI and API never create such a backup and refuse to download one (`403 BACKUP_CONTAINS_MASTER_KEY`); copy it on the device.

## Create a backup

On the device:

```bash
sudo crewquarters backup create                        # -> /var/lib/crewquarters/backups
sudo crewquarters backup create --out /media/usb --retention 5
sudo crewquarters backup create --include-master-key --out /media/usb   # sensitive
sudo crewquarters backup list
sudo crewquarters backup verify /media/usb/crewquarters-backup-20260925T100000Z.tar.gz
```

The CLI runs `cq-admin backup create` in a one-shot platform container (the image ships the PostgreSQL 16 client). Archives in the default directory are given to the control API's user so the UI lists and downloads them; an archive with the master key stays root-only.

From the UI: **System > Backups > Create backup** (owner only). It runs as a `system.backup` job in the control API (`POST /api/v1/system/backups`, CSRF and `Idempotency-Key` supported; one backup at a time, `409 BACKUP_IN_PROGRESS` otherwise), writes into `CQ_BACKUP_DIR`, and keeps the newest `CQ_BACKUP_RETENTION` (default 7). `GET /api/v1/system/backups` lists API and device backups with status and size; `GET /api/v1/system/backups/{id}/download` streams one (owner only, audited as `system.backup_downloaded`).

Inside any platform container: `cq-admin backup create --out DIR [--label L] [--retention N] [--include-master-key --master-key-file PATH]`.

Before an event (PLAN §26), take a backup, copy it off the device, and run `crewquarters backup verify` on the copy.

## Restore

Restore replaces **all** platform data. It is CLI-only (the UI shows these instructions).

```bash
sudo crewquarters backup restore /var/lib/crewquarters/backups/<file>.tar.gz --stop
sudo crewquarters up
```

What it does:

1. Refuses while the platform is running (exit code 3) unless `--stop` is given; with `--stop` it stops every service except PostgreSQL. Inside, `cq-admin backup restore` also refuses while any other session is connected to the database.
2. Takes a **pre-restore backup** of the current data (`...-pre-restore.tar.gz` in the backups directory) unless `--no-pre-backup`.
3. Verifies the archive: only the expected files, and every part's SHA-256 and size against the manifest.
4. Checks migration compatibility: the backup's migration head must be one this version knows (the same or an older schema). A backup made by a newer Crewquarters is refused (`BACKUP_TOO_NEW`): install that version first. Database downgrades are not supported.
5. Drops and recreates the `public` schema and runs `pg_restore --single-transaction --exit-on-error`.
6. Replaces the document store's contents (in-flight uploads in `.staging` are kept) and gives the files the store's owner and directory mode.
7. Runs `alembic upgrade head`, so an older backup ends at the current schema.
8. With `--with-master-key`, and only if the backup has one, rewrites `/etc/crewquarters/master.key` in place (its owner and mode are kept).
9. Checks every stored secret against the device master key and marks the ones it cannot decrypt for reconnection (see above). Records `system.backup_restored` in the audit log.

Then start the platform with `crewquarters up` and open **Connections** if the restore listed any provider to reconnect.

## Diagnostics bundle

```bash
sudo crewquarters diagnostics --out /tmp --tail 500
```

A zip (`0600`) with versions, `docker compose ps`, service health and readiness, disk and memory, GPU (`nvidia-smi` summary), model states, recent failed runs and dead jobs (IDs, states and error codes only; no payloads, results, prompts or transcripts), the configuration with every secret masked, systemd status, the runtime daemon journal, and the last N log lines of every service.

The host part is collected as plain files and streamed into `cq-admin diagnostics --host-tar -` inside the platform, so everything, host logs included, passes through the same redaction (`crewquarters_shared.redaction.scrub`): bearer/basic credentials, cookies, OAuth codes and state, query-string secrets, `key=value`/JSON secret fields, database URL passwords, OpenAI/Anthropic/Google/Twilio credential shapes, JWTs, long hex and random tokens, email addresses, and phone numbers. The device's own configured secrets (`CQ_SECRET_KEY`, the internal service token, the database password, ...) are also masked literally wherever they appear.

**Download diagnostics** in System Status calls `GET /api/v1/system/diagnostics` (owner only, audited). The control API cannot read other containers' logs, so that bundle holds its own recent logs plus health from every service; use the CLI for all services' logs.

## Demo reset (real platform)

Between rehearsals (PLAN §24):

```bash
sudo crewquarters demo reset                 # asks for confirmation
sudo crewquarters demo reset --yes --since 2026-10-01T09:00:00+00:00
sudo crewquarters demo reset --yes --schedules --knowledge
```

It cancels active runs and waits for them to stop (`--timeout`, default 60 s; `--force` deletes runs that did not stop), releases chat model leases, and deletes runs with their attempts, events, input requests, telephony calls and action claims, finished run jobs, and chat sessions with their messages. Users, installations, schedules, connections, models, knowledge bases and the audit log are kept; `--schedules` and `--knowledge` also remove those. It then reseeds: re-syncs the bundled agent catalog and moves each schedule's next run to its next occurrence after now (no misfire runs). The reset is audited as `demo.reset`.

## Tests

- `services/control_api/tests/test_backup_restore.py`: archive format and permissions, master key only on request, tamper and path checks, retention, restore round trip from an older head, refusal while connected, newer-version refusal, connection marking with a different key, restore with the key, and the owner API (create, idempotency, in-progress, worker, list, download, audit, master-key refusal, failure reporting). pg_dump/pg_restore run in the test database's container through `docker exec`.
- `services/control_api/tests/test_diagnostics.py`: seeds secrets into logs, host files, configuration and a failed run's error, and asserts none appear in the bundle.
- `services/control_api/tests/test_demo_reset.py`.
- `tests/stack/test_backup_restore_stack.py` (`CQ_STACK_TESTS=1`, marker `docker`): brings up the laptop Compose stack as project `cqbak` on ports 18082/15434 with its own image tags, creates an owner, installation, run, schedule, knowledge document, chat session and OpenAI key, takes backups with the appliance CLI and the API, destroys all volumes, restores (with and without the master key), and checks the data, then `docker compose restart` and `down`/`up`. A physical reboot is not possible in CI: the systemd boot path is covered by the `.deb` install test (`infra/debian/test-install.sh`), and this test covers the Compose-level restart.
