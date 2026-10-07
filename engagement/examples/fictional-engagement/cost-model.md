# Cost model

## Basis

- Engagement / revision / estimator role / date: Synthetic Reference Assistant /
  1 / Operations owner / 2030-04-01
- Currency / tax basis: AUD / exclusive of tax
- Pricing source / region / validity: invented planning allowances, not provider
  prices; hypothetical Australia East. Not valid for purchasing or provisioning.
- Workload: 20 synthetic documents at most 1 MiB each, 50 quality cases, 10
  warm-ups and 100 measured load requests. Subsequent staging use limited to
  1,000 requests/month, at most 2,000 input and 500 output tokens per request,
  concurrency 2, 40 active hours/month, 1 GiB storage, 7-day runtime traces.
- A-03 requires current SKU/model rates, always-on service costs, regional
  availability and quota validation before provisioning.

## One-time delivery cost

| Item             | Quantity       | Unit rate      | Subtotal  |
| ---------------- | -------------- | -------------- | --------- |
| Planned delivery | 9 person-days  | 800 AUD/day    | 7,200 AUD |
| Included reserve | 1 person-day   | 800 AUD/day    | 800 AUD   |
| Total fixed fee  | 10 person-days | Not applicable | 8,000 AUD |

The fee includes the five-working-day post-sign-off correction obligation
defined in [scope](fixed-price-scope.md); it is not an extra charge.

## Recurring operating estimate

| Component               | Usage / sizing basis                                                             | Rate basis                                | Monthly estimate |
| ----------------------- | -------------------------------------------------------------------------------- | ----------------------------------------- | ---------------- |
| Model and embeddings    | Up to 2 million input and 0.5 million output tokens; bounded synthetic ingestion | Invented allowance, not token pricing     | 30 AUD           |
| Search                  | One staging index over 20 documents; include provisioned hours                   | Invented allowance, not SKU pricing       | 40 AUD           |
| Compute                 | 40 active hours; idle capacity also to be priced                                 | Invented allowance, not compute pricing   | 20 AUD           |
| Storage and database    | 1 GiB and small audit workload                                                   | Invented allowance, not capacity pricing  | 20 AUD           |
| Monitoring and transfer | 7-day trace retention and small egress                                           | Invented allowance, not ingestion pricing | 10 AUD           |
| Total                   | One month                                                                        | Not applicable                            | 120 AUD          |

## Guardrails and sensitivity

- Billing owner / cap: Operations owner / 200 AUD per month, separate from the
  8,000 AUD delivery fee.
- Warning at 140 AUD: reforecast and notify Sponsor.
- Stop-review at 180 AUD: stop new requests and ingestion, then scale down or
  remove chargeable staging resources with approved operational procedures.
  Budget alerts alone are not spend enforcement; residual charges may continue.
- Usage limits: enforce request and token ceilings; verify the stop procedure
  in AC-07. This is a proposed control, not an implemented automation claim.
- Sensitivity: doubling the model/embedding allowance changes the illustrative
  total to 150 AUD; doubling every component changes it to 240 AUD, above cap.
  Always-on Search/database costs may exceed these invented allowances entirely.
- Exclusions: tax, support, discounts, production, real data and later slices.
- Reforecast: weekly, at either alert, or before any model, tier or workload
  change. Do not provision until A-03 is verified or a new cap is approved.

References: [scope](fixed-price-scope.md),
[requirements](non-functional-requirements.md).
