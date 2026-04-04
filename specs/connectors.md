# Connectors for Gamma

## Purpose

Gamma should not learn every application domain's file layout or internal scripts. Instead, Gamma should call stable connectors that expose domain operations.

## Why this matters

Gamma already has a clean adapter mindset for LLM, STT, TTS, memory, and tools. Connectors extend the same idea to external domains such as the career/job application system.

## Recommended layering

- Gamma conversation/orchestration decides intent
- Gamma tool wrapper packages a connector operation request
- connector adapter executes domain logic
- connector returns structured data plus source references
- Gamma turns the result into user-facing language and follow-up questions

## First connector target

The first serious connector target should be the job application system because:
- it already has file-based structure and schemas
- it benefits from stable, repeatable operations
- it carries meaningful safety requirements
- it is a strong later use case for Gamma-assisted workflows

See:
- `../connectors/job_application/CONNECTOR_SPEC.md`
- `../connectors/job_application/schemas/`

## Suggested Gamma integration path

### Phase A: thin wrapper
Add one Gamma tool that forwards operation envelopes to a local connector adapter.

Suggested tool name:
- `job_application_connector`

### Phase B: typed client
Add a small client module under a future path like:
- `gamma/integrations/connectors/job_application_client.py`

The client should expose typed methods such as:
- `health()`
- `get_profile()`
- `create_job(...)`
- `ingest_posting(...)`
- `parse_posting(...)`
- `map_question(...)`
- `generate_autofill_bundle(...)`

Current thin implementation now exists at:
- `gamma/integrations/job_application_client.py`
- `../connectors/job_application/local_files.py`

### Phase C: browser handoff
When browser automation exists, Gamma should request a reviewed autofill bundle from the connector first, then hand that bundle to the browser layer. Submission should remain approval-gated.

## Rules to preserve

- Gamma remains the orchestrator, not the domain owner
- connector adapters remain swappable
- review-first stays the default for job applications
- source-of-truth facts stay in the career system, not duplicated inside Gamma
