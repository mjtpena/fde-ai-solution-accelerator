# ADR-0007: Azure AI Content Safety screens prompts, retrieved chunks and answers, failing closed

**Status:** Accepted
**Date:** 2026-10-09

## Context

Spec §6.2 puts a content-safety step in the grounded-answer workflow
(generate → validate citations → content safety → respond), and spec §7 lists direct
and indirect prompt injection as threats (`threat-model/threats.yml` T-003, T-004).
Before this decision the defences were structural only: retrieved text is wrapped as
untrusted data (`security_core.prompt_injection`, C-004), tool policy and approvals sit
outside the model, and citations are validated. Nothing classified the user's prompt,
the retrieved documents or the generated answer, so a jailbreak prompt reached the
model, a poisoned chunk stayed in the evidence, and a harmful answer (T-022) reached
the user.

Constraints: no new agent framework (ADR-0001), Azure calls only in `infrastructure/`
adapters behind a protocol, managed identity only with key auth disabled, retrieved
text is untrusted, and nothing the classifier sees may leak into telemetry.

## Decision

Screen every grounded-answer turn with **Azure AI Content Safety**, at three points,
inside `GroundedAnswerWorkflow` (`packages/agent_core/workflows/grounded_answer.py`):

1. **Prompt Shields on the user prompt, before retrieval.** An attack yields a refusal
   with code `content_safety_prompt_attack`. Nothing is retrieved and no tool is
   offered.
2. **Prompt Shields on every retrieved chunk, before the sufficiency check.** Attacked
   chunks are **dropped**, never rewritten, and the remaining evidence goes through the
   normal sufficiency gate, so the turn abstains (`insufficient_evidence`) when nothing
   usable is left. Dropped chunk IDs (never their text) are recorded on the result, the
   spans and a log event.
3. **Harm-category analysis of the answer, after citation validation.** Hate,
   SelfHarm, Sexual and Violence are requested with `outputType=FourSeverityLevels`
   (severities 0, 2, 4, 6). A category at or above its threshold withholds the answer
   with code `content_safety_output_blocked`.

Further decisions:

- **Port and policy in `security_core`.** `ContentSafetyChecker`
  (`shield_prompt`, `analyze_text`), the value types, the stable reason codes and
  `ContentSafetyPolicy` live in
  `packages/security_core/src/accelerator/security_core/content_safety/`. The default
  threshold is **4 (medium) for every category**, matching the Azure OpenAI default
  content filter. Thresholds are per category, `API_CONTENT_SAFETY_BLOCK_SEVERITY_*`,
  bounded to 1–6 so no category can be silently disabled.
- **REST adapter over `httpx`, no SDK.** `apps/api/src/accelerator/infrastructure/content_safety.py`
  calls `POST {endpoint}/contentsafety/text:shieldPrompt` and `text:analyze` at
  `api-version=2024-09-01` (GA) with a bearer token for
  `https://cognitiveservices.azure.com/.default` from the injected managed-identity
  credential. It splits input to the documented limits (user prompt 10,000 characters;
  at most five documents and 10,000 characters per Prompt Shields request; 10,000
  characters per analysis; overlapping pieces), runs the requests concurrently, and
  bounds each call by `min(API_CONTENT_SAFETY_TIMEOUT_SECONDS, time left before
  ExecutionContext.deadline_utc)`.
- **Fail closed.** Any transport error, timeout, non-200 status, malformed or
  incomplete reply, or elapsed deadline raises `ContentSafetyUnavailableError`, which
  the workflow turns into a refusal with code `content_safety_unavailable`. The
  workflow never returns an unscreened answer when a checker is configured.
- **Always on in production.** `API_CONTENT_SAFETY_ENABLED` defaults to `true`;
  production settings validation requires it and an HTTPS
  `API_CONTENT_SAFETY_ENDPOINT`, and the composition root refuses to build an
  unscreened workflow outside development and test. Development without an endpoint
  runs unscreened and logs `content_safety_unscreened`.
- **Contract.** Refusals are abstentions with fixed, non-echoing reason texts. The SSE
  `abstention` frame gains a required `code` (`insufficient_evidence`,
  `answer_withdrawn`, and the three `content_safety_*` codes); the OpenAPI contract
  and web client types are regenerated. Streaming keeps the existing design: tokens are
  forwarded as the model produces them, and output screening runs after generation, so
  a blocked answer that already streamed is withdrawn by the `abstention` frame that
  clients must honour (as for failed citation validation).
- **Audit.** Every content-safety refusal writes an `audit_event` of type
  `content_safety` with the actor, correlation ID and a bounded `reason_code`
  (`apps/api/src/accelerator/api/refusal_audit.py`, migration
  `0007_content_safety_audit`). A failed audit write fails the turn.
- **Telemetry.** `content_safety.shield_prompt` and `content_safety.analyze` CLIENT
  spans carry the decision, stage, counts, dropped chunk IDs, blocked categories and
  per-category severities, never text (`docs/observability-standard.md`).
- **Infrastructure.** `infrastructure/modules/content-safety.bicep` (kind
  `ContentSafety`, SKU parameter, `disableLocalAuth: true`, custom subdomain for Entra
  tokens, diagnostics to Log Analytics, public network access like the other AI
  services per ADR-0006) and Cognitive Services User
  (`a97b65f3-24c7-4388-baec-2e87135dc908`) at account scope for the API identity and
  the evaluation principal.
- **Evaluation.** `make eval-smoke` runs the real wiring with a deterministic
  `OfflineContentSafetyChecker` and adds a seventh, **hard** gate, `content_safety`
  (baseline 1.0, tolerance 0): jailbreak rows refused before retrieval, poisoned chunks
  dropped, harmful answers withheld, and no false-positive refusal or drop on benign
  rows. `injection_followed` now also reruns injection rows with document shielding
  blind, so the evidence wrapper (C-004) is still measured as defence in depth.

## Consequences

Positive:

- The §6.2 workflow step exists and is enforced by the workflow, not by prompts.
- Direct and indirect injection have a classifier layer in front of the structural
  defences; a classifier miss still meets the evidence wrapper, which the smoke gate
  keeps measuring.
- Outages refuse instead of answering unscreened; refusals are audited and traceable.

Negative:

- Two to three extra service calls per turn add latency and cost; the request deadline
  bounds them, and a slow service turns into refusals, not slow answers.
- Streamed tokens can reach the client before a blocked answer is withdrawn. Clients
  must discard text on `abstention`. Buffering the whole answer would remove this but
  give up token streaming.
- Prompt Shields false positives drop legitimate chunks or refuse legitimate prompts.
  The smoke gate catches regressions in wiring, not classifier quality; projects should
  add representative rows to their full evaluation.
- The two vendor-notes injection rows in the smoke dataset now expect abstention,
  because both chunks that could answer them are poisoned and dropped.
- The Foundry hosted agent (ADR-0002) composes its workflow through a configured
  factory; it is screened only if that factory supplies a checker.

## Alternatives considered

- **Heuristics only (`injection_signals`).** Easy to evade, and they cannot rate harm.
  Kept for telemetry and for the offline checker, not as the production control.
- **Azure OpenAI / Foundry built-in content filters only.** They act on the model
  call, cannot drop individual retrieved chunks before sufficiency, and give the
  workflow no typed verdict to audit or evaluate. They remain a complementary layer.
- **The `azure-ai-contentsafety` SDK.** A new dependency for two POST calls; the
  workspace already ships `httpx`, which also makes the contract tests
  (`httpx.MockTransport`) straightforward.
- **Fail open on outage.** Rejected: an outage would silently ship unscreened answers.
- **Rewrite or redact flagged chunks.** Rejected for the same reason
  `security_core.prompt_injection` never rewrites evidence: silently changed evidence
  corrupts answers and citations.

## References

- `docs/spec.md` §6.2, §7, §8
- `packages/security_core/src/accelerator/security_core/content_safety/__init__.py`
- `packages/agent_core/workflows/grounded_answer.py`
- `apps/api/src/accelerator/infrastructure/content_safety.py`
- `apps/api/src/accelerator/api/refusal_audit.py`
- `packages/evaluation_core/runners/smoke.py`, `packages/evaluation_core/runners/offline.py`
- `infrastructure/modules/content-safety.bicep`,
  `infrastructure/modules/content-safety-user-role-assignment.bicep`
- `threat-model/controls.yml` C-029; `threat-model/threats.yml` T-003, T-004, T-022
- Azure AI Content Safety Prompt Shields quickstart:
  https://learn.microsoft.com/azure/ai-services/content-safety/quickstart-jailbreak
- Azure AI Content Safety text moderation quickstart:
  https://learn.microsoft.com/azure/ai-services/content-safety/quickstart-text
