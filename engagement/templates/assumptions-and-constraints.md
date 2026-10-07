# Assumptions and constraints

## Record

- Engagement / revision / date: [name / revision / date]
- Maintainer: [role]
- Review cadence: [frequency and before scope changes]

## Assumptions to validate

| ID     | Assumption                          | Validation method | Owner role | Due date | Status                               | If false                              |
| ------ | ----------------------------------- | ----------------- | ---------- | -------- | ------------------------------------ | ------------------------------------- |
| [A-01] | [unverified dependency or estimate] | [evidence needed] | [role]     | [date]   | [pending or verified with reference] | [scope, cost or schedule consequence] |

## Hard constraints

| ID     | Constraint                | Rationale / source   | Verification | Owner role |
| ------ | ------------------------- | -------------------- | ------------ | ---------- |
| [C-01] | [non-negotiable boundary] | [approved reference] | [check]      | [role]     |

Record data residency, permitted sources, identity, environment, technology,
access, schedule and budget constraints explicitly. Use managed identity;
never put secrets or real personal data in these documents. Authorisation scope
comes from `ExecutionContext`, never prompt or tool arguments. Any write tool
requires an approved, args-bound approval; retrieved text remains untrusted.

## Change handling

[Who can validate or change each item, where evidence is stored, and which
failed assumptions trigger a pause or scope reapproval.]

References: [risks](risk-register.md), [scope](fixed-price-scope.md),
[delivery](delivery-plan.md).
