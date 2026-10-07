# Cost model

Use this template to estimate and review the Azure service and model costs for an
engagement. Select SKUs for the target region and workload; do not treat the
example guardrail defaults as a price quote or a production capacity plan.

## Deployment assumptions

| Input | Value |
| --- | --- |
| Environment and Azure region | `<environment>` / `<region>` |
| Monthly active users | `<count>` |
| Requests per user per day | `<count>` |
| Average input tokens per request | `<count>` |
| Average output tokens per request | `<count>` |
| Peak concurrent requests | `<count>` |
| Pricing source and checked date | `<Azure pricing calculator URL>` / `<YYYY-MM-DD>` |

## Azure service selections

| Service | SKU / deployment type | Capacity settings | Pricing source |
| --- | --- | --- | --- |
| Azure AI Search | `searchSkuName: <SKU>` | `searchReplicaCount: <count>` replicas, `searchPartitionCount: <count>` partitions | `<regional price source>` |
| Microsoft Foundry account | `foundrySkuName: <SKU>` | `<regional price source>` |
| Foundry model deployment | `modelSkuName: <deployment SKU>` | `modelDeploymentName: <name>`, `modelName: <name>`, `modelVersion: <version>`, `modelFormat: <format>`, `modelCapacity: <count>` | `<regional/model price source>` |

Pass the selected values through the deployment's Bicep parameters; avoid
hard-coding SKUs in resource modules. Search service, Foundry account, and model
deployment SKUs are separate settings and can have different availability and
billing dimensions. Confirm the supported value and price for the target region
before deployment.

## Request guardrails

| Setting | Environment variable | Initial API default | Engagement value |
| --- | --- | ---: | ---: |
| Maximum tokens reserved per request | `API_REQUEST_TOKEN_BUDGET` | 8,192 | `<tokens>` |
| Requests per resolved user/scope set per rolling window | `API_REQUEST_RATE_LIMIT` | 60 | `<requests>` |
| Rolling rate-limit window | `API_REQUEST_RATE_WINDOW_SECONDS` | 60 seconds | `<seconds>` |

The API cost-guard dependency obtains `user_id`, `scope_ids`, and
`correlation_id` from the server-resolved `ExecutionContext`. Callers should
reserve the maximum input plus output tokens before a model operation and settle
the reservation with the actual usage. Never accept identity or scope values
from a request body or model arguments.

The built-in in-memory rate limiter is local to one API process. Deployments
with multiple API workers or replicas must inject a shared `RateLimiter`
implementation if the engagement requires a global per-user limit.

## Estimate

| Cost component | Monthly quantity | Unit price | Estimated monthly cost |
| --- | ---: | ---: | ---: |
| Azure AI Search | `<service hours/capacity>` | `<price>` | `<amount>` |
| Foundry input tokens | `<input token quantity>` | `<price per unit>` | `<amount>` |
| Foundry output tokens | `<output token quantity>` | `<price per unit>` | `<amount>` |
| Other applicable Azure services | `<quantity>` | `<price>` | `<amount>` |
| **Estimated total** |  |  | **`<amount>`** |

Review the estimate against measured usage after deployment and revisit SKUs,
capacity, request limits, and token budgets when workload assumptions change.

## Azure validation

- Confirm each selected service and model deployment SKU is available in the
  target subscription and region, then run the deployment's Bicep build and
  what-if checks.
- In the target subscription, verify the Azure Cost Management budget threshold
  and alert recipients/action group. This template and local checks do not
  create or deliver Azure cost alerts.
- Record the validated SKU, pricing source, alert threshold, and checked date
  with the engagement estimate. Subscription, deployment, and alert-delivery
  checks require Azure access and cannot be verified by local tests.
