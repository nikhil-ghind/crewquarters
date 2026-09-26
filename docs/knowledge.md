# Knowledge service

Owner: Nikhil Sajan Khaneja (Person 3). Code: `services/knowledge` (`crewquarters_knowledge`). Tables: `knowledge_bases`, `documents`, `document_chunks`.

The service indexes uploaded documents on the device and answers retrieval queries scoped to one knowledge base, with citations. It is the only container with the documents mount.

## Pipeline (PLAN.md section 9.1)

1. **Validate** each upload:
   - **Type:** `.txt`, `.md`, `.csv` (UTF-8), text-based `.pdf`, or `.docx`. The content must match the extension: `%PDF-`, a Word zip, and no NUL bytes in text.
   - **File name:** only the base name is kept, with control characters and traversal removed.
   - **Size:** `CQ_MAX_UPLOAD_BYTES`.
   - **Duplicates:** the same bytes twice in one knowledge base return `409 DUPLICATE_DOCUMENT`.
2. **Store:** stream to `.staging`, hash with SHA-256, then `os.replace` into `CQ_DOCUMENTS_DIR/<kb>/<document>.<ext>`. The upload returns `202` with the document in state `PENDING`, and a `knowledge.ingest` job is queued on the shared job queue.
3. **Extract**, keeping locators:

   | Format | Locator |
   | --- | --- |
   | PDF | page |
   | Markdown | section (heading) and line |
   | docx | section, paragraph, table row |
   | CSV | row, as `header: value; ...` |
   | text | line |

   Scanned PDFs (no text layer) fail with `SCANNED_PDF_UNSUPPORTED`; there is no OCR. Encrypted PDFs, zip bombs (over 100 MB uncompressed or over 5,000 entries), and malformed files fail with clear codes.
4. **Normalize:** NFKC, no control characters, collapsed spaces. Line breaks and headings survive.
5. **Chunk:** about 800 tokens with 120 tokens of overlap, preferring to end at a paragraph or segment boundary and never crossing documents. Tokens are approximated as words and punctuation marks.
6. **Embed** on the CPU in batches of 32 (see below).
7. **Index:** chunks and vectors are written in one transaction, and only then is the document marked `READY`.
   - Permanent problems mark it `FAILED` with `{code, message}`.
   - Other errors retry with backoff (`PENDING` plus `INGEST_RETRYING`), up to 3 attempts, and then `FAILED`.

**Resource limits (PLAN.md section 16.1).** A small upload can take unbounded time or memory to parse; for example, a 50 KB `.docx` can inflate to megabytes of XML. So steps 3-5 run in a child process (`python -m crewquarters_knowledge.isolation`), and ingestion is bounded:

| Limit | Default | On breach |
| --- | --- | --- |
| Wall clock for extraction and chunking; the child is killed | `CQ_EXTRACT_TIMEOUT_SECONDS` = 120 | `EXTRACTION_TIMEOUT` |
| The child's address space (`RLIMIT_AS`), plus CPU time just above the timeout | `CQ_EXTRACT_MEMORY_BYTES` = 1 GiB | `DOCUMENT_TOO_COMPLEX` |
| Uncompressed `word/document.xml`, checked before parsing | 4 MiB | `DOCUMENT_TOO_COMPLEX` |
| docx paragraphs plus table rows | 50,000 | `DOCUMENT_TOO_COMPLEX` |
| Extracted characters | 5,000,000 | `DOCUMENT_TOO_COMPLEX` |
| Chunks | 2,500 | `DOCUMENT_TOO_COMPLEX` |
| Whole ingestion (extract, embed, index) | `CQ_INGEST_MAX_SECONDS` = 900 | `INGEST_TIMEOUT` (not retried) |

All of these fail the document permanently. The job lease is heartbeated only until `CQ_INGEST_MAX_SECONDS`, so a stuck worker cannot hold a job forever. An embedding batch that is already running when that deadline passes still finishes in its thread, but no further batches start. The limits rely on Linux `setrlimit`, which the service's `amd64` and `arm64` containers provide.

**Abandoned documents.** If the knowledge process dies mid-ingestion and the scheduler's reaper marks the job `dead`, nothing else would finish the document. Every `CQ_INGEST_SWEEP_SECONDS`, the worker fails `PENDING` or `PROCESSING` documents that have no available or claimed `knowledge.ingest` job and have not changed for a minute. They get `INGEST_ABANDONED`; re-indexing retries them.

**Deletion (PLAN.md section 6.1).** Deleting a document or knowledge base removes the rows and, in the same transaction, queues a `knowledge.purge` job. The worker then overwrites each file with zeros, flushes it to disk, and removes it. A failure retries with backoff, up to 10 attempts, and a path outside `CQ_DOCUMENTS_DIR` is refused. Overwriting is best effort: SSDs and copy-on-write filesystems can keep old blocks, so the appliance also relies on full-disk encryption. Re-indexing replaces all chunks.

Secrets are not files: they are ciphertext rows in `encrypted_secrets`, removed when a connection is deleted. Backups contain only ciphertext, and the master key is kept out of them.

## Embeddings

| Mode (`CQ_EMBEDDING_MODE`) | Profile | Notes |
| --- | --- | --- |
| `local` | `local.embedding.jina-v2-small-en` | `jinaai/jina-embeddings-v2-small-en` through `fastembed` 0.8 (ONNX Runtime, CPU), pinned to one revision (below). 512 dimensions and an 8192-token context, so an 800-token chunk is never truncated. Apache-2.0, about 130 MB. Wheels exist for `linux/amd64` and `linux/arm64`. |
| `fake` | `fake.hashing-512` | Deterministic feature hashing, 512 dimensions. For tests and the laptop `dev` profile only: it matches words, not meaning. |

### The pinned model (PLAN.md section 16.1)

fastembed runs the ONNX export in the Hugging Face repository `Xenova/jina-embeddings-v2-small-en`. The service pins commit `523cadcb9c2e71c7153fc46016e1fe79acb4f58f`, which was that repository's `main` on 2025-04-24. It uses five files, each with a pinned size and SHA-256 in `crewquarters_knowledge.embeddings.MODEL_FILES`: `config.json`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, and `onnx/model.onnx` (SHA-256 `8daf59ca…1614e05`).

- **Install:** `cq-knowledge fetch-model [--dir PATH]` downloads those files from that commit into `CQ_EMBEDDING_MODEL_DIR/jina-embeddings-v2-small-en@523cadcb9c2e71c7153fc46016e1fe79acb4f58f/`.
  - It checks each file's size and SHA-256 and moves it into place only if both match.
  - It is idempotent: files that are already present and intact are not downloaded again, and damaged ones are replaced.
  - Exit codes: `0` when every file is verified, `1` on a checksum mismatch, and `2` when the download fails.
  - Deployment runs it once, in a one-shot init container or the installer, with network access. `CQ_EMBEDDING_MODEL_DIR` must be a persistent volume: writable for `fetch-model`, and read-only is enough for the service.
- **Load:** the service verifies the files again and loads them **at startup** from that directory only, with `HF_HUB_OFFLINE=1` and fastembed's `specific_model_path`. It never downloads anything, and nothing goes to `/tmp`.
- **Missing or altered model:** the service still starts and `/health/live` passes. `/health/ready` returns `503` with `{"code": "EMBEDDING_MODEL_UNAVAILABLE", "message": "... Run cq-knowledge fetch-model ..."}`, and the reason is logged. Ingestion jobs stay queued, not failed, queries return `503 EMBEDDING_MODEL_UNAVAILABLE`, and the worker looks for the model again every minute.
- **Profile metadata:** readiness and every knowledge base (`embeddingModel`) report the profile's source and revision. The profile id stands for exactly this revision. The revision is the one `main` served before pinning, so vectors indexed earlier stay valid and existing rows need no change. Moving to another revision needs a new profile id; existing knowledge bases then report `EMBEDDING_PROFILE_MISMATCH` until they are re-indexed.

A knowledge base records its profile and dimension, which never change. Querying a knowledge base under a different profile returns `409 EMBEDDING_PROFILE_MISMATCH`; re-index it first. The vector column is fixed at `vector(512)`, so a profile with another dimension needs a migration. Search is exact cosine distance over one knowledge base's `READY` chunks. PLAN.md defers the HNSW index until the corpus is large enough to need it.

## Internal API (`/internal/v1`, service token)

Callers are the control API (UI pages and uploads), the capability broker (agent `knowledge.search:config`), and the model gateway (RAG chat). Each caller authorizes the knowledge base first; this service enforces the knowledge-base scope of every query.

| Route | Purpose |
| --- | --- |
| `POST /knowledge-bases` `{ownerId, name}` | Create (409 on duplicate name) |
| `GET /knowledge-bases`, `GET/DELETE /knowledge-bases/{id}` | List / read / delete with files |
| `POST /knowledge-bases/{id}/documents` (multipart `file`) | Upload → `202` document (`PENDING`) |
| `GET /knowledge-bases/{id}/documents`, `GET/DELETE /documents/{id}` | States, extraction summary, errors |
| `POST /documents/{id}/reindex` | Queue re-indexing |
| `POST /knowledge-bases/{id}/query` | Retrieval (below) |
| `GET /metrics` | Prometheus text: `cq_http_*`, `cq_knowledge_retrieval_duration_seconds` (target p95 < 1 s), `cq_knowledge_ingests_total{outcome}`, `cq_knowledge_ingest_duration_seconds`, `cq_knowledge_purges_total{outcome}`, `cq_knowledge_documents{state}`, `cq_knowledge_chunks`. Labels never carry names, IDs, or query text |

Knowledge bases are returned as `{id, name, embeddingProfile, embeddingDimension, embeddingModel, createdAt}`, where `embeddingModel` is the pinned model behind the profile (`{model, source, revision, dimension}`). Documents are returned as `{id, knowledgeBaseId, name, mime, bytes, sha256, state, extracted, error, createdAt, updatedAt}`. The filesystem path is never returned.

### Query

Request (PLAN.md section 9.2): `{"query": "...", "topK": 8, "maxContextTokens": 5000, "filters": {"documentIds": []}}`. Passages are ranked by cosine similarity and returned in order until adding the next one would exceed `maxContextTokens`. Each passage has the `Passage` shape of `packages/contracts/broker-sdk.openapi.yaml`, plus a human-readable `location`.

```json
{
  "knowledgeBaseId": "…",
  "passages": [
    {"citationId": "<chunk uuid>", "text": "…", "score": 0.83,
     "document": {"id": "…", "name": "policy.md"},
     "locator": {"section": "Cancellation", "line": 1}, "location": "Cancellation (line 1)"}
  ],
  "context": "UNTRUSTED EVIDENCE. …\n<evidence>\n<passage id=\"…\" document=\"policy.md\" location=\"…\">\n…\n</passage>\n</evidence>"
}
```

`context` is the prompt-injection-safe form for a model (PLAN.md section 9.3):
- It is labelled as untrusted evidence and delimited.
- It tells the model not to follow instructions inside it and to cite passages by id.
- Passage text is XML-escaped, so a document cannot close its own passage or forge another.

Put `context` in a user or tool message, never in the system instruction. The UI resolves a `citationId` to an authorized document preview.

## Configuration

These are in addition to the shared `CQ_*` settings.

| Variable | Type | Default | Secret | Profiles | Purpose |
| --- | --- | --- | --- | --- | --- |
| `CQ_DOCUMENTS_DIR` | path | `/var/lib/crewquarters/documents` | no (holds personal data) | all | Document bytes; the only read/write mount |
| `CQ_MAX_UPLOAD_BYTES` | int | 26214400 (25 MiB) | no | all | Per-document limit |
| `CQ_EMBEDDING_MODE` | `fake` \| `local` | `fake` | no | dev: fake; demo-cpu, dgx: local | Embedding profile |
| `CQ_EMBEDDING_MODEL_DIR` | path | `/var/lib/crewquarters/embedding-models` | no | demo-cpu, dgx | Pinned embedding model, filled by `cq-knowledge fetch-model`. A persistent volume, writable only for `fetch-model`; the service reads it offline |
| `CQ_CHUNK_TOKENS` / `CQ_CHUNK_OVERLAP_TOKENS` | int | 800 / 120 | no | all | Chunking |
| `CQ_INGEST_LEASE_SECONDS` / `CQ_INGEST_POLL_SECONDS` | int / float | 60 / 1.0 | no | all | Ingestion job lease and idle poll |
| `CQ_EXTRACT_TIMEOUT_SECONDS` / `CQ_EXTRACT_MEMORY_BYTES` | float / int | 120 / 1073741824 (1 GiB) | no | all | Wall clock and address space of the extraction child process |
| `CQ_INGEST_MAX_SECONDS` | float | 900 | no | all | One ingestion gives up after this long; the lease heartbeat stops then |
| `CQ_INGEST_SWEEP_SECONDS` | float | 60 | no | all | How often documents without a live job are failed |
| `CQ_KNOWLEDGE_HOST` / `CQ_KNOWLEDGE_PORT` | string / int | `0.0.0.0` / `8000` | no | all | Listen address (container network) |

## Tests

Run with `pytest services/knowledge/tests`. Fixture documents are generated in code: text PDFs, a scanned PDF, docx with a heading and a table, CSV, Markdown, and injection text.

- **Extraction:** every format; malformed, encrypted, scanned, and oversized inputs; traversal names.
- **Chunking:** chunk size, overlap, boundary snapping, and locators.
- **Retrieval:** cited results, scoped to one knowledge base, with a token budget.
- **Injection:** passage text cannot forge evidence tags.
- **Lifecycle:** deletion and re-index, retry on embedding failure, and profile mismatch.
- **Limits:** the child process, its timeout and memory limit, the docx, character and chunk caps, the ingestion deadline, and the abandoned-document sweep.
- **Model:** `fetch-model` against a local HTTP server (checksums, idempotence, exit codes), offline loading, and readiness while the model is missing. No test needs the network or the real model.

## Finding documents by file name from an agent

An agent with `knowledge: [config]` can find documents in its knowledge base by file-name pattern, then
search only those. The broker route is `GET /internal/v1/sdk/knowledge/documents` (capability
`knowledge.search:config`; the base must be the one chosen in the installation config). It matches a
case-insensitive glob (`*`, `?`, `[abc]`) against each `READY` document's name, newest first, up to 200.

```python
kb = ctx.knowledge.connect()                     # the base the owner selected (or connect("<id>"))
policies = await kb.find_files("policy-*.md")    # glob, matched on the broker
recent = await kb.find_files("*.pdf", regex=r"-20(25|26)\.pdf$")   # plus a regex, applied in the SDK
policies.files, policies.total, policies.truncated
result = await kb.search("refund window", files="policy-*.md")     # search only those files
```

`find_files` returns `KnowledgeFile(id, name, mime, bytes)` items. With a `regex`, the SDK asks for up to 200
glob matches and filters them itself, so `truncated` says when more matched than were considered. The regex is
matched anywhere in the name and is case-sensitive unless you add `(?i)`. A search limited to files that match
nothing returns no passages without calling the model or the index. `connect()` needs exactly one granted
base; pass the id if there could be more.
