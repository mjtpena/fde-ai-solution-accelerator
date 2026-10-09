# Observability standard

This file is the register of telemetry the accelerator emits. `docs/spec.md` section 8
defines the target trace shape. This document records what the code emits today and
the rules for adding more. Custom span attributes use the `fde.*` namespace and must
be listed here. Where an OpenTelemetry GenAI semantic convention exists, use it.

Everything below is taken from the code. An attribute, span or event that is not in
this file is not part of the contract. Section 8 also lists items that are **not
emitted yet**; they are collected under [Gaps against the spec](#gaps-against-the-spec).

## Principles

1. **One correlation ID per request, end to end.** `CorrelationIdMiddleware`
   (`apps/api/src/accelerator/identity/scope_resolver.py`) accepts a client-supplied
   `X-Correlation-ID` only if it parses as a UUID; otherwise it rejects the request
   with 400. Without the header it generates a UUID. The ID is stored in
   `request.state.correlation_id` and returned in the `X-Correlation-ID` response header.
   `TracingMiddleware`
   (`packages/observability_core/src/observability_core/middleware.py`) runs inside
   that boundary. It reuses the same ID and stamps it on every span as
   `fde.correlation_id`. The ID then flows into `ExecutionContext.correlation_id`,
   structured log lines, audit events, approval records, and the `correlation_id`
   field of 429 error bodies (`apps/api/src/accelerator/api/cost_guard.py`). When
   `TracingMiddleware` runs on its own (without the outer boundary), it ignores
   caller-supplied IDs and generates a fresh UUID.
2. **No content in telemetry.** Telemetry never contains prompts, model responses,
   retrieved document text, queries, tool arguments, raw search filter values,
   headers, URLs, baggage, exception messages or stack traces. The span API enforces
   this by construction. `SpanAttributes`
   (`packages/observability_core/src/observability_core/attributes.py`) is a strict,
   frozen, `extra="forbid"` Pydantic model with no content fields. Span names are
   bounded ASCII identifiers. Exceptions record only `error.type` (the class name).
   Content capture cannot be switched on.
3. **Redaction at the export boundary (defence in depth).** API spans are exported
   through `SanitizingSpanExporter`
   (`packages/observability_core/src/observability_core/export.py`). It runs
   `security_core.redaction.redact_attributes`
   (`packages/security_core/src/accelerator/security_core/redaction/__init__.py`)
   over copies of every span attribute and event attribute. API log lines pass every
   string field through `redact_sensitive_data`
   (`apps/api/src/accelerator/telemetry/logging.py`). The redaction removes bearer
   tokens, JWTs, credential assignments, known token formats and email addresses. It
   also replaces values whose key contains `authorization`, `credential`, `key`,
   `password`, `passwd`, `secret` or `token`.
4. **Metadata only.** Attributes are counts, enums, booleans, durations, model
   deployment names and filter *field names*. Record the actual outcome, not an
   assumed success. Emit a span only for an operation that actually ran.
5. **No secrets in configuration.** Telemetry export authenticates with managed
   identity only. Application Insights local (key) authentication is disabled.
6. **Pinned instrumentation.** The GenAI conventions are still changing, so versions
   are pinned in `packages/observability_core/pyproject.toml`:
   `opentelemetry-api==1.44.0`, `opentelemetry-sdk==1.44.0` and
   `azure-monitor-opentelemetry-exporter==1.0.0b57`. The `gen_ai.*` names below are
   the contract for those versions.

## Trace shape and span inventory

The tracer is `Telemetry`
(`packages/observability_core/src/observability_core/tracing.py`). Its instrumentation
scope is `fde.observability_core` (version `0.1.0`). It sets the resource attribute
`service.name`, which the API sets to `fde-accelerator-api`
(`apps/api/src/accelerator/telemetry/tracing.py`). `Telemetry.span()` accepts only the
operations in its `Operation` literal. The `workflow`, `tool` and `approval`
operations require a bounded identifier suffix (`workflow.<name>`, and so on).

Trace of a chat request today:

```text
http.request                    SERVER    TracingMiddleware
├── workflow.grounded_answer    INTERNAL  TracedChatTurn
│   ├── content_safety.shield_prompt  CLIENT  TracedContentSafetyChecker (prompt)
│   ├── retrieval.search        CLIENT    TracedRetriever
│   ├── content_safety.shield_prompt  CLIENT  TracedContentSafetyChecker (documents)
│   ├── retrieval.sufficiency   INTERNAL  TracedSufficiencyChecker
│   ├── gen_ai.chat             CLIENT    TracedAnswerGenerator
│   ├── citations.validate      INTERNAL  TracedCitationValidator
│   └── content_safety.analyze  CLIENT    TracedContentSafetyChecker
└── response                    INTERNAL  TracingMiddleware
```

| Span name | Kind | Emitted by | Attributes set |
|---|---|---|---|
| `http.request` | SERVER | `TracingMiddleware` through `Telemetry.request` (`packages/observability_core/src/observability_core/middleware.py`, `tracing.py`), installed in `apps/api/src/accelerator/api/app.py` | `http.request.method`, `http.response.status_code`, `fde.correlation_id`, `fde.duration_ms`, `error.type` (on exception) |
| `response` | INTERNAL | `TracingMiddleware` through `Telemetry.response`. A direct child of `http.request`, created before the app runs, so it covers processing and all streaming sends | `http.response.status_code`, `fde.correlation_id`, `fde.duration_ms`, `error.type` |
| `workflow.grounded_answer` | INTERNAL | `TracedChatTurn` (`apps/api/src/accelerator/telemetry/traced.py`), wired with `name="grounded_answer"` in `apps/api/src/accelerator/api/composition.py` | `fde.outcome`, `fde.abstention.code`, `fde.content_safety.dropped_chunk_ids`, `fde.correlation_id`, `fde.duration_ms`, `error.type` |
| `retrieval.search` | CLIENT | `TracedRetriever` (`apps/api/src/accelerator/telemetry/traced.py`), wired in `apps/api/src/accelerator/infrastructure/grounded_answer.py` | `fde.retrieval.top_k`, `fde.retrieval.filter_fields`, `fde.retrieval.result_count`, `fde.retrieval.injection_signal_count`, `fde.correlation_id`, `fde.duration_ms`, `error.type` |
| `retrieval.sufficiency` | INTERNAL | `TracedSufficiencyChecker` (`apps/api/src/accelerator/telemetry/traced.py`) | `fde.retrieval.decision`, `fde.retrieval.result_count`, `fde.correlation_id`, `fde.duration_ms`, `error.type` |
| `gen_ai.chat` | CLIENT | `TracedAnswerGenerator` (`apps/api/src/accelerator/telemetry/traced.py`) | `gen_ai.operation.name`, `gen_ai.request.model`, `fde.citations.count`, `fde.correlation_id`, `fde.duration_ms`, `error.type` |
| `citations.validate` | INTERNAL | `TracedCitationValidator` (`apps/api/src/accelerator/telemetry/traced.py`) | `fde.citations.count`, `fde.citations.valid`, `fde.correlation_id`, `fde.duration_ms`, `error.type` |
| `content_safety.shield_prompt` | CLIENT | `TracedContentSafetyChecker` (`apps/api/src/accelerator/telemetry/traced.py`), wired in `apps/api/src/accelerator/infrastructure/grounded_answer.py`. Twice per turn: the prompt before retrieval, then the retrieved chunks | `fde.content_safety.stage`, `fde.content_safety.document_count`, `fde.content_safety.decision`, `fde.content_safety.prompt_attack`, `fde.content_safety.dropped_chunk_ids`, `fde.content_safety.error_reason`, `fde.correlation_id`, `fde.duration_ms`, `error.type` |
| `content_safety.analyze` | CLIENT | `TracedContentSafetyChecker`. Once per answered turn, after citation validation | `fde.content_safety.decision`, `fde.content_safety.blocked_categories`, `fde.content_safety.severity.<Category>`, `fde.content_safety.error_reason`, `fde.correlation_id`, `fde.duration_ms`, `error.type` |

`Telemetry` sets the span kind: `CLIENT` for `retrieval.search`, `gen_ai.chat` and
the two `content_safety.*` operations,
`SERVER` for `http.request`, and `INTERNAL` for everything else. On any exception a
span gets status `ERROR` and `error.type`. Exception events and stack traces are not
recorded (`record_exception=False`). An HTTP status of 500 or above also sets `ERROR`
on `http.request` and `response`.

Propagation: only the W3C `traceparent` header is extracted. There must be exactly
one, and it must be at most 512 bytes. Invalid or duplicate values start a new trace.
Baggage is never read.

Non-HTTP entry points (for example, workers) must open
`telemetry.request(correlation_id)` with a UUID generated at that entry point. No
worker does this today.

## Attribute reference

### `fde.*` custom attributes

| Attribute | Type | Meaning | Emitted by |
|---|---|---|---|
| `fde.correlation_id` | string (UUID) | Request correlation ID, set on every span opened inside `Telemetry.request` | `Telemetry._span` (`packages/observability_core/src/observability_core/tracing.py`) |
| `fde.duration_ms` | double | Wall-clock duration of the span's operation in milliseconds, set on every span | `Telemetry._span` (`tracing.py`) |
| `fde.retrieval.top_k` | int (> 0) | Requested maximum number of search results | `TracedRetriever` through `SpanAttributes.top_k` (`apps/api/src/accelerator/telemetry/traced.py`) |
| `fde.retrieval.filter_fields` | string[] | Sorted metadata filter **field names**. Never values or raw filter expressions. Omitted when the request has no filters | `TracedRetriever` through `SpanAttributes.filter_fields` |
| `fde.retrieval.result_count` | int (≥ 0) | On `retrieval.search`, the number of evidence items returned. On `retrieval.sufficiency`, the number of evidence IDs the checker kept | `TracedRetriever`, `TracedSufficiencyChecker` |
| `fde.retrieval.injection_signal_count` | int (≥ 0) | Number of retrieved items whose text matches `security_core.prompt_injection.injection_signals` (instruction-like phrasing). Detection only; evidence is always handled as untrusted data | `TracedRetriever` |
| `fde.retrieval.decision` | enum `sufficient` / `insufficient` (the contract also allows `abstain`) | Outcome of the evidence sufficiency check | `TracedSufficiencyChecker` |
| `fde.citations.count` | int (≥ 0) | On `gen_ai.chat`, the number of citations the model returned. On `citations.validate`, the number of citations checked | `TracedAnswerGenerator`, `TracedCitationValidator` |
| `fde.citations.valid` | boolean | `true` if every cited chunk ID was retrieved in this turn. `false` if validation raised | `TracedCitationValidator` |
| `fde.outcome` | enum `success` / `abstained` / `denied` (the contract also allows `failure`) | Workflow result. `success` means answered, `abstained` means an abstention, and `denied` means the turn ended in a pending approval (`ApprovalRequired`) | `TracedChatTurn` |
| `fde.abstention.code` | enum `insufficient_evidence` / `content_safety_prompt_attack` / `content_safety_output_blocked` / `content_safety_unavailable` | Stable cause of an abstention or refusal (`Abstention.code`) | `TracedChatTurn` |
| `fde.content_safety.stage` | enum `prompt` / `documents` | What a `content_safety.shield_prompt` span screened | `TracedContentSafetyChecker` |
| `fde.content_safety.document_count` | int (≥ 0) | Number of retrieved chunks sent to Prompt Shields | `TracedContentSafetyChecker` |
| `fde.content_safety.decision` | enum `allow` / `attack` / `block` / `unavailable` | Screening verdict. `attack` (Prompt Shields) refuses the prompt or drops chunks; `block` (analysis) withholds the answer; `unavailable` refuses (fail closed) | `TracedContentSafetyChecker` |
| `fde.content_safety.prompt_attack` | boolean | Prompt Shields flagged the user prompt | `TracedContentSafetyChecker` |
| `fde.content_safety.dropped_chunk_ids` | string[] | Chunk IDs flagged as document attacks and dropped from evidence. IDs only, never text. Also on `workflow.grounded_answer` | `TracedContentSafetyChecker`, `TracedChatTurn` |
| `fde.content_safety.blocked_categories` | string[] | Harm categories at or above their block threshold | `TracedContentSafetyChecker` |
| `fde.content_safety.severity.<Category>` | int (0–7) | Severity per category (`Hate`, `SelfHarm`, `Sexual`, `Violence`) | `TracedContentSafetyChecker` |
| `fde.content_safety.error_reason` | string | Why screening had no verdict (`timeout`, `transport_error`, `http_<status>`, `malformed_response`, `incomplete_analysis`, `deadline_elapsed`) | `TracedContentSafetyChecker` |

`SpanAttributes` also defines two `fde.*` attributes that no production code sets
yet: `fde.retrieval.reason_code` (a bounded identifier) and `fde.tool.risk` (`read` /
`write` / `destructive`). They exist only in tests and in the package README example.
They are reserved for the `retrieval.sufficiency` and `tool.<name>` spans and must
keep these names when they are wired.

### OpenTelemetry semantic-convention attributes

| Attribute | Type | Meaning | Emitted by |
|---|---|---|---|
| `gen_ai.operation.name` | string, always `chat` | GenAI operation type | `Telemetry.span("gen_ai.chat")` (`tracing.py`) |
| `gen_ai.request.model` | string | Model **deployment name** (`API_FOUNDRY_MODEL_DEPLOYMENT`) | `TracedAnswerGenerator` through `SpanAttributes.model` |
| `gen_ai.usage.input_tokens` | int | Prompt tokens | Mapped in `SpanAttributes` and asserted in `packages/observability_core/tests/test_tracing.py`. **Not set by the API yet** (see gaps) |
| `gen_ai.usage.output_tokens` | int | Completion tokens | As above. **Not set by the API yet** |
| `http.request.method` | string | HTTP method. Values outside the standard set become `_OTHER` | `Telemetry.request` |
| `http.response.status_code` | int | Response status | `TracingMiddleware` |
| `error.type` | string | Exception class name only | `Telemetry._span` |
| `service.name` | string (resource) | `fde-accelerator-api` | `Telemetry.create` / `apps/api/src/accelerator/telemetry/tracing.py` |

No `gen_ai.prompt`, `gen_ai.completion`, message-content attributes or events are
emitted. `packages/security_core/redaction/test_redaction.py` uses
`gen_ai.prompt` only as a test fixture, to prove that redaction would scrub it.

### Non-conforming attributes (to fix)

`validate_citations` (`packages/retrieval_core/citations/__init__.py`) writes
`citations.validation.valid`, `citations.validation.cited_count`,
`citations.validation.unknown_count` and `citations.validation.retrieved_count` to an
optional span. These names are outside the `fde.*` namespace. Production code never
supplies that span: `SameTurnCitationValidator()` is built without a `span_factory` in
`apps/api/src/accelerator/infrastructure/grounded_answer.py` and
`packages/evaluation_core/runners/smoke.py`. So today they appear only in
`tests/test_citation_validation.py`. Before wiring a span factory, rename them to
`fde.citations.*`.

## Structured log events

The API logs through `JsonFormatter` (`apps/api/src/accelerator/telemetry/logging.py`),
which `configure_logging` installs on the root and uvicorn loggers
(`apps/api/src/accelerator/api/main.py`). It writes one JSON object per line to stderr
with these keys:

* `timestamp`, `level`, `logger` and `event` (the log message, which is a static
  snake_case event name);
* every `extra=` key, with string values redacted;
* `exception_type` when `exc_info` is present. The exception message and traceback
  are never written.

Container Apps sends stdout and stderr to Log Analytics (see
[Export configuration](#export-configuration)).

### API events

| Event | Level | Structured fields | Emitted from |
|---|---|---|---|
| `scope_resolved` | INFO | `correlation_id` | `apps/api/src/accelerator/identity/scope_resolver.py` |
| `scope_repository_unconfigured` | ERROR | `correlation_id` | `apps/api/src/accelerator/identity/scope_resolver.py` |
| `scope_resolution_failed` | ERROR | `correlation_id` | `apps/api/src/accelerator/identity/scope_resolver.py` |
| `unexpected_request_failure` | ERROR | `correlation_id`, `exception_type` | `apps/api/src/accelerator/identity/scope_resolver.py` |
| `jwks_key_skipped` | WARNING | `kid`, `correlation_id` | `apps/api/src/accelerator/identity/jwt_validator.py` |
| `jwks_refresh_failed` | WARNING | `exception_type`, `serving_stale_keys`, `correlation_id` | `apps/api/src/accelerator/identity/jwt_validator.py` |
| `search_scope_violation` | ERROR | `correlation_id` (and `event`) | `apps/api/src/accelerator/infrastructure/search/adapter.py` |
| `search_index_provisioned` | INFO | `index_name` | `apps/api/src/accelerator/infrastructure/search/provision.py` |
| `chat_turn_ended_with_pending_approval` | WARNING | `correlation_id`, `exception_type` (via `exc_info`) | `apps/api/src/accelerator/api/tool_turns.py` |
| `streamed_answer_withdrawn` | ERROR | `correlation_id`, `exception_type` | `apps/api/src/accelerator/api/chat.py` |
| `content_safety_refusal` | WARNING | `correlation_id`, `reason_code` | `apps/api/src/accelerator/api/refusal_audit.py` |
| `content_safety_refusal_unaudited` | ERROR | `correlation_id`, `reason_code` (development without a database only) | `apps/api/src/accelerator/api/refusal_audit.py` |
| `content_safety_chunks_dropped` | WARNING | `correlation_id`, `chunk_ids` | `apps/api/src/accelerator/api/refusal_audit.py` |
| `content_safety_unscreened` | WARNING | `environment`, `content_safety_enabled` (development and test only; production refuses to start) | `apps/api/src/accelerator/infrastructure/grounded_answer.py` |
| `database_unconfigured` | WARNING | `environment` | `apps/api/src/accelerator/api/composition.py` |
| `approval_decision_refused` | INFO | `correlation_id`, `reason` (exception class name) | `apps/api/src/accelerator/api/approvals.py` |
| `readiness_check_failed` | WARNING | `check`, `exception_type`, `correlation_id` | `apps/api/src/accelerator/api/health.py` |
| `audit_repository_unconfigured` | ERROR | `correlation_id` | `apps/api/src/accelerator/api/audit.py` |
| `audit_query_failed` | ERROR | `correlation_id` | `apps/api/src/accelerator/api/audit.py` |
| `auth_failure_audit_suppressed` | WARNING | `correlation_id`, `suppressed_count` | `apps/api/src/accelerator/api/audit.py` |
| `auth_failure_audit_failed` | ERROR | `correlation_id` | `apps/api/src/accelerator/api/audit.py` |

### Ingestion worker events

Logger name: `ingestion_worker`.

| Event | Level | Structured fields | Emitted from |
|---|---|---|---|
| `worker_started`, `worker_stopped` | INFO | JSON in the message body: `timestamp`, `level`, `event`, `worker` (`ingestion`) | `log_event` in `workers/ingestion/src/ingestion_worker/__main__.py` |
| `ingestion_disabled` | WARNING | `reason` | `workers/ingestion/src/ingestion_worker/composition.py` |
| `ingestion_message_invalid` | ERROR | `message_id`, `error_count` | `workers/ingestion/src/ingestion_worker/consumer.py` |
| `ingestion_rejected` | WARNING | `message_id`, `document_id`, `attempt`, `reason` | `workers/ingestion/src/ingestion_worker/consumer.py` |
| `ingestion_poisoned` | ERROR | `message_id`, `document_id`, `attempt`, `reason` (`<ExceptionType> after N attempt(s)`) | `workers/ingestion/src/ingestion_worker/consumer.py` |
| `ingestion_retry_scheduled` | WARNING | `message_id`, `document_id`, `attempt`, `reason`, `delay_seconds` | `workers/ingestion/src/ingestion_worker/consumer.py` |
| `ingestion_message_completed` | INFO | `message_id`, `document_id`, `attempt` | `workers/ingestion/src/ingestion_worker/consumer.py` |

Known gap: the worker entry point configures `logging.basicConfig(format="%(message)s")`
instead of `JsonFormatter`. Its `extra=` fields are attached to the log record but
are **not written** to the output today. Only `worker_started` and `worker_stopped`,
which put their JSON in the message, come out structured. The worker has no
correlation ID and opens no spans.

### Evaluation events

| Event | Level | Structured fields | Emitted from |
|---|---|---|---|
| `evaluation_smoke_row_failed` | WARNING | `correlation_id` (`evaluation-smoke-<row_id>`), `row_id`, `failed_gates`, `error_type` | `packages/evaluation_core/runners/smoke.py` |
| Smoke report summary (format string `correlation_id=evaluation-smoke ...`) | INFO / ERROR | `extra=context`, values in the message | `packages/evaluation_core/reporting/smoke.py` |
| Full evaluation summary (format string) | INFO | Values in the message | `packages/evaluation_core/evaluators/full.py` |

The evaluation summaries use printf-style messages instead of static event names.
Treat them as CLI output, not as part of the event contract.

## Metrics

The application defines **no OpenTelemetry metrics**. No `Meter`, counter or
histogram exists in the code. The section 8 metrics (request and error rates,
P50/P95 latency per span type, tokens per request, abstention rate, approval rate)
must be derived from traces in Application Insights or Log Analytics:

* Latency per span type: the span `duration` or `fde.duration_ms`, grouped by span name.
* Error rate: span status, `http.response.status_code` ≥ 500, `error.type`.
* Abstention rate: `fde.outcome == "abstained"` on `workflow.*` spans, or
  `fde.retrieval.decision == "insufficient"`.
* Pending-approval rate: `fde.outcome == "denied"` on `workflow.*` spans.

Approval decisions themselves are recorded as audit events, not telemetry.

Platform metrics (Container Apps, PostgreSQL) go to Log Analytics through
`AllMetrics` diagnostic settings in `infrastructure/modules/container-apps.bicep`
and `infrastructure/modules/postgres.bicep`. The evaluation pass rate by build comes
from the `make eval-smoke` report (`evaluations/reports/smoke.json`), not from runtime
telemetry.

## Export configuration

* **Resources.** `infrastructure/modules/monitoring.bicep` creates a Log Analytics
  workspace with `disableLocalAuth: true` and a workspace-based Application Insights
  component with `DisableLocalAuth: true`. Ingestion therefore requires an Entra token.
* **Identity.** `infrastructure/modules/monitoring-publisher-role-assignment.bicep`
  grants **Monitoring Metrics Publisher**, scoped to the Application Insights
  component, to the API and migrator identities (`infrastructure/main.bicep`).
* **Destination.** The deployment injects `APPLICATIONINSIGHTS_CONNECTION_STRING`
  (`infrastructure/modules/container-apps.bicep`). With local auth disabled it is not
  a credential. The API reads it as a `SecretStr`
  (`apps/api/src/accelerator/configuration/settings.py`, also accepting
  `API_APPLICATIONINSIGHTS_CONNECTION_STRING`). Production settings validation fails
  if it is missing.
* **Exporter.** `ApplicationInsightsAdapter`
  (`packages/observability_core/src/observability_core/infrastructure/application_insights.py`)
  builds `AzureMonitorTraceExporter` with `ManagedIdentityCredential`. The
  user-assigned client ID comes from `API_MANAGED_IDENTITY_CLIENT_ID` or
  `AZURE_CLIENT_ID`. It uses `ApplicationInsightsSampler(1.0)` (full sampling) and
  `disable_offline_storage=True`. Spans go through a `BatchSpanProcessor`, so there is
  no network I/O on the request path. `build_telemetry`
  (`apps/api/src/accelerator/telemetry/tracing.py`) always wraps the exporter in
  `SanitizingSpanExporter(redact_attributes)`.
* **Without a connection string** (development and test), spans are created but not
  exported. Tests pass an in-memory exporter through `span_exporter`.
* **Logs.** Container Apps environment logs go to Azure Monitor
  (`appLogsConfiguration.destination: 'azure-monitor'`). A `diagnosticSettings`
  resource sends `allLogs` and `AllMetrics` to the Log Analytics workspace. Application
  logs are not sent through the OpenTelemetry exporter.

## Gaps against the spec

| Spec item (section 8) | Status |
|---|---|
| `authz.resolve_scope` span | Allowed by `Telemetry.span` but not emitted. Scope resolution (`ScopeResolver.resolve`) logs `scope_resolved` instead |
| `tool.<name>` span with `fde.tool.risk` | Not emitted. Tool policy (`packages/agent_core/middleware/tool_policy.py`) and tool turns (`apps/api/src/accelerator/api/tool_turns.py`) are untraced |
| `approval.<transition>` spans | Not emitted. Approval transitions are audited in the database (`apps/api/src/accelerator/infrastructure/approvals.py`) |
| `gen_ai.usage.input_tokens` / `output_tokens` | Mapped but not set. `TracedAnswerGenerator` records only the model name and citation count |
| `fde.retrieval.reason_code` on sufficiency | Mapped but not set |
| Runtime metrics | None. Derive them from traces (see [Metrics](#metrics)) |
| Worker traces and structured logs | The worker opens no spans and drops `extra=` fields (see above) |

## Adding telemetry

1. **Use the API, not raw spans.** Open spans with `Telemetry.span(operation, name=...,
   attributes=SpanAttributes(...))`. A new span type needs a new member of the
   `Operation` literal in `packages/observability_core/src/observability_core/tracing.py`.
   A new attribute needs a typed, bounded field and an OTel name in
   `SpanAttributes.to_otel`. Use `span.set_attribute` on a returned span only for
   values that can only be known after the operation (counts, decisions), and use
   only names already listed here.
2. **Naming.** Use an OpenTelemetry semantic-convention name (`gen_ai.*`, `http.*`,
   `error.*`) when one exists for the pinned version. Otherwise use
   `fde.<area>.<name>` in lowercase snake case. Never invent new top-level
   namespaces (see the `citations.validation.*` debt above).
3. **Values.** Only counts, enums, booleans, durations, static identifiers and field
   names. Never prompts, completions, retrieved text, queries, tool arguments, filter
   values, user identifiers beyond the correlation ID, scope IDs, tokens, headers,
   URLs or exception messages. Span names must be static. Do not build them from
   user or model input.
4. **Logs.** Use a static snake_case event name as the message and put context in
   `extra=`. Always include `correlation_id` when one exists. Log exception
   *types* (`type(exc).__name__`), never `str(exc)` or tracebacks in fields.
5. **No content-capturing auto-instrumentation.** Do not add instrumentors (for
   example GenAI or HTTP client instrumentors that capture message content) without
   a privacy review. Any new instrumentation package must be pinned to an exact
   version.
6. **Document and test.** Every new span, attribute or event name goes in this file
   in the same pull request, with its type, meaning and emitting file. Add a test
   that asserts it on an in-memory exporter, following
   `packages/observability_core/tests/test_tracing.py` or
   `apps/api/tests/test_telemetry.py`. Redaction behaviour is covered by
   `packages/security_core/redaction/test_redaction_export.py` and
   `apps/api/tests/test_telemetry.py`. Then run `make check` and `make eval-smoke`.
