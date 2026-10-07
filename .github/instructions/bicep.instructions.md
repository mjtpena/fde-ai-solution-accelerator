---
applyTo: "infrastructure/**"
---
# Bicep rules
- One module per resource family in `infrastructure/modules/`. Parameters via `.bicepparam`.
- User-assigned managed identity per container app; least-privilege RBAC role assignments.
- Disable local/key auth where the service supports it (Search, Storage, Foundry).
- No secrets as outputs. No hard-coded names, regions or SKUs.
- Diagnostic settings to Log Analytics on every resource.
- `az bicep build` and `what-if` must succeed in CI.
