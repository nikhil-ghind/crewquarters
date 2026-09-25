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

**Deletion.** Deleting a document or knowledge base removes the rows first, then the bytes. Re-indexing replaces all chunks.

## Embeddings

| Mode (`CQ_EMBEDDING_MODE`) | Profile | Notes |
| --- | --- | --- |
| `local` | `local.embedding.jina-v2-small-en` | Pinned `jinaai/jina-embeddings-v2-small-en` through `fastembed` 0.8 (ONNX Runtime, CPU). 512 dimensions and an 8192-token context, so an 800-token chunk is never truncated. Apache-2.0, about 120 MB, downloaded once into `CQ_EMBEDDING_CACHE_DIR`. Wheels exist for `linux/amd64` and `linux/arm64`. |
| `fake` | `fake.hashing-512` | Deterministic feature hashing, 512 dimensions. For tests and the laptop `dev` profile only: it matches words, not meaning. |

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

Documents are returned as `{id, knowledgeBaseId, name, mime, bytes, sha256, state, extracted, error, createdAt, updatedAt}`. The filesystem path is never returned.

### Query

Request (PLAN.md section 9.2): `{"query": "...", "topK": 8, "maxContextTokens": 5000, "filters": {"documentIds": []}}`. Passages are ranked by cosine similarity and returned in order until adding the next one would exceed `maxContextTokens`.

```json
{
  "knowledgeBaseId": "…",
  "passages": [
    {"citationId": "<chunk uuid>", "documentId": "…", "documentName": "policy.md",
     "location": "Cancellation (line 1)", "locator": {"section": "Cancellation", "line": 1},
     "text": "…", "score": 0.83}
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
| `CQ_EMBEDDING_CACHE_DIR` | path | fastembed default | no | demo-cpu, dgx | Model download cache (pre-populate for offline devices) |
| `CQ_CHUNK_TOKENS` / `CQ_CHUNK_OVERLAP_TOKENS` | int | 800 / 120 | no | all | Chunking |
| `CQ_INGEST_LEASE_SECONDS` / `CQ_INGEST_POLL_SECONDS` | int / float | 60 / 1.0 | no | all | Ingestion job lease and idle poll |
| `CQ_KNOWLEDGE_HOST` / `CQ_KNOWLEDGE_PORT` | string / int | `0.0.0.0` / `8000` | no | all | Listen address (container network) |

## Tests

Run with `pytest services/knowledge/tests`. Fixture documents are generated in code: text PDFs, a scanned PDF, docx with a heading and a table, CSV, Markdown, and injection text.

- **Extraction:** every format; malformed, encrypted, scanned, and oversized inputs; traversal names.
- **Chunking:** chunk size, overlap, boundary snapping, and locators.
- **Retrieval:** cited results, scoped to one knowledge base, with a token budget.
- **Injection:** passage text cannot forge evidence tags.
- **Lifecycle:** deletion and re-index, retry on embedding failure, and profile mismatch.
