# ADR-0004: Bind approvals to the tool, canonical arguments, scope, requester and expiry

**Status:** Accepted
**Date:** 2026-10-09

## Context

Design principle 2 (spec §1) says any state-changing tool needs human approval bound
to the exact arguments. The threats in spec §7 include excessive agency and approval
replay or tampering. Common weak designs include:

- approving a tool *name* or a free-text description, so the model or a client can
  change the arguments after approval
- approvals that never expire
- retries that run the side effect twice
- the person who asked for an action approving it themselves

The model must not decide risk, and it must not be able to supply scope, approval or
identity data through tool arguments.

## Decision

**Policy outside the model.** Each `EnterpriseTool` declares a `ToolRisk`.

- `ToolPolicy` (`packages/security_core/src/accelerator/security_core/tool_policy/policy.py`)
  maps `read_only` to execute, and `low_impact_write`, `high_impact_write` and
  `privileged` to approval. It rejects `prohibited` and unknown values.
- `ToolRegistry` (`packages/agent_core/tools/registry.py`) refuses to register
  `PROHIBITED` tools. It also refuses write or privileged tools that are not an
  `IdempotentWriteTool`, and argument models that allow extra fields or contain
  scope or project fields.

**One path to execution.** The agent sees tools only through `as_agent_tool`
(`packages/agent_core/tools/agent_bridge.py`).

- That wrapper validates arguments with the tool's Pydantic model and calls
  `ToolPolicyMiddleware.invoke` (`packages/agent_core/middleware/tool_policy.py`).
- The middleware enforces per-turn and per-session call limits. The API uses a
  shared PostgreSQL counter. It caps each call's timeout at the remaining request
  deadline.
- A write with no approval returns `ApprovalRequired` instead of running. Only one
  approval can be requested per turn; after that, further tool calls in the turn
  return a "paused" message.

**The approval record** (`packages/agent_core/approvals/models.py`,
`ApprovalService.create`) stores:

- `tool_name`
- `args_hash`: SHA-256 of canonical JSON, built by `canonical_args_hash` from
  `model_dump(mode="json")` with sorted keys, compact separators and no NaN
- `scope_id`: creation requires exactly one server-resolved scope
- `requested_by`: from `ExecutionContext.user_id`
- `expires_at`: default TTL 10 minutes; must be timezone-aware and in the future
- `correlation_id`
- status, starting as `pending`

The bound arguments themselves are not persisted or returned by the API, only their
hash.

**Decisions** (`ApprovalService.approve` / `reject`) run under a row lock
(`SELECT ... FOR UPDATE` in `apps/api/src/accelerator/infrastructure/approvals.py`):

- The approval's scope must be in the decider's scopes. If it is not, the API answers
  404, the same as for a missing approval.
- The decider must hold the **`Approver`** role.
- **Separation of duties:** the requester cannot approve or reject their own request.
- A pending approval past its expiry becomes `expired` instead of being decided.

`apps/api/src/accelerator/api/approvals.py` exposes list, approve and reject, all
behind the `Approver` role.

**Execution** (`ApprovalService.execute`) runs in one transaction under the row
lock. It:

1. recomputes the hash and rejects any change of tool name, arguments or scope
   (`ApprovalMismatchError`)
2. expires stale approvals
3. requires status `approved`
4. runs the middleware's `validate_approval` callback against the *persisted* row.
   That callback checks that the executing user is the requester and that the scope
   is authorised. For `privileged` tools it also requires a distinct decider whose
   context holds a configured privileged-approver role.
5. calls `IdempotentWriteTool.execute_approved(..., execution_id=approval.id)`
6. marks the approval `executed`

Every transition writes an `ApprovalAuditEvent`.

**Idempotency.** The approval UUID is the stable execution ID and is reused on retry.
`IdempotentWriteTool` (`packages/agent_core/tools/base.py`) requires each
implementation to store that key together with its scope, atomically with the effect
and result. The row lock alone is documented as *not* sufficient for exactly-once
external effects.

## Consequences

Positive:

- Changing any argument, or reusing an approval in another scope or for another tool,
  fails closed. Replaying an approval that has already executed raises
  `ApprovalReplayError`.
- Self-approval is impossible in the service, not just hidden in the UI
  (`ApprovalView.can_decide`).
- The offline smoke gate's `approval_bypass` hard gate checks every write execution
  against an executed approval with the same tool, arguments hash and scope
  (ADR-0005).

Negative / trade-offs:

- Any argument change, even a trivial one, needs a fresh approval. A 10-minute default
  TTL can expire before a busy approver acts.
- Exactly-once depends on each write tool's own durable deduplication. A downstream
  system without an idempotency facility cannot host a write tool under this
  contract.
- Approvals need a single scope, so cross-scope writes are not supported.
- Every write decision needs a second person with the `Approver` role. Small teams
  need at least two such people.
- The accelerator is domain-free and ships no concrete write tool, and the API has no
  HTTP endpoint that executes an approved write. The execute path exists in
  `ToolPolicyMiddleware` and `ApprovalService`. Projects add their write tools and the
  execution trigger.

## Alternatives considered

- **Approve by tool name or session.** Simpler, but it allows argument tampering after
  approval (spec §7, approval replay / tampering).
- **Let the model ask for confirmation in conversation.** Policy would sit inside the
  model, and an injected document could satisfy it.
- **Hash the raw JSON the model sent.** Key order and whitespace would change the
  hash. Hashing the validated, canonicalised Pydantic dump is stable.
- **A new idempotency key per attempt.** A retry after a crash would run the effect a
  second time. Reusing the approval UUID makes retries deduplicable.
- **Allow requester self-approval for low-impact writes.** Rejected for the baseline.
  Every decision needs the `Approver` role and a second person. Projects may relax
  this only through their own reviewed change.

## References

- `docs/spec.md` §1 (principle 2), §5.2, §5.3, §6.3, §7
- `packages/agent_core/approvals/service.py`, `packages/agent_core/approvals/models.py`
- `packages/agent_core/middleware/tool_policy.py`
- `packages/agent_core/tools/agent_bridge.py`, `packages/agent_core/tools/base.py`,
  `packages/agent_core/tools/registry.py`
- `packages/security_core/src/accelerator/security_core/tool_policy/`
- `apps/api/src/accelerator/api/approvals.py`
- `apps/api/src/accelerator/infrastructure/approvals.py`
- `apps/api/src/accelerator/api/tool_turns.py`
