# Cost model

## Basis

- Engagement / revision / estimator role / estimate date: [values]
- Currency / tax basis: [currency / inclusive or exclusive]
- Pricing source / region / validity: [approved source, region, expiry]
- Workload: [documents, volume, requests, tokens, concurrency, hours, retention]
- Assumptions: [IDs, model/version and pricing uncertainty]

## One-time delivery cost

| Item               | Quantity      | Unit rate      | Subtotal                      |
| ------------------ | ------------- | -------------- | ----------------------------- |
| [delivery effort]  | [person-days] | [currency/day] | [quantity multiplied by rate] |
| [included reserve] | [person-days] | [currency/day] | [quantity multiplied by rate] |
| Total fixed fee    | [total days]  | Not applicable | [sum]                         |

## Recurring operating estimate

| Component               | Usage / sizing basis                 | Rate basis         | Monthly estimate |
| ----------------------- | ------------------------------------ | ------------------ | ---------------- |
| Model and embeddings    | [input/output tokens and ingestion]  | [rates and source] | [amount]         |
| Search                  | [tier, replicas and hours]           | [rate and source]  | [amount]         |
| Compute                 | [CPU/memory and active/idle hours]   | [rate and source]  | [amount]         |
| Storage and database    | [capacity, operations and retention] | [rate and source]  | [amount]         |
| Monitoring and transfer | [ingestion, retention and egress]    | [rate and source]  | [amount]         |
| Total                   | [period]                             | Not applicable     | [sum]            |

## Guardrails and sensitivity

- Billing owner / monthly cap: [role / amount]
- Warning / stop-review thresholds: [amounts and actions]
- Enforcement: [how usage is limited; budget alerts alone do not stop charges]
- Sensitivity: [effect of higher tokens, traffic, retention or always-on capacity]
- Exclusions: [tax, support, discounts and other excluded charges]
- Reforecast trigger: [threshold, cadence and owner]

Separate fixed delivery fees from consumption estimates. Obtain current
provider pricing before approval; do not treat estimates as a spending guarantee.

References: [scope](fixed-price-scope.md),
[non-functional requirements](non-functional-requirements.md).
