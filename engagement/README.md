# Engagement artefacts

These domain-free consulting artefacts cover the 12 templates in
[spec section 4](../docs/spec.md#4-repository-structure). They describe delivery
decisions, not new application entities or business workflows.

| Artefact                    | Blank template                                       | Filled fictional example                                                |
| --------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------- |
| Problem statement           | [Template](templates/problem-statement.md)           | [Example](examples/fictional-engagement/problem-statement.md)           |
| Discovery workshop agenda   | [Template](templates/discovery-workshop-agenda.md)   | [Example](examples/fictional-engagement/discovery-workshop-agenda.md)   |
| Stakeholder map             | [Template](templates/stakeholder-map.md)             | [Example](examples/fictional-engagement/stakeholder-map.md)             |
| Smallest valuable slice     | [Template](templates/smallest-valuable-slice.md)     | [Example](examples/fictional-engagement/smallest-valuable-slice.md)     |
| Assumptions and constraints | [Template](templates/assumptions-and-constraints.md) | [Example](examples/fictional-engagement/assumptions-and-constraints.md) |
| Non-functional requirements | [Template](templates/non-functional-requirements.md) | [Example](examples/fictional-engagement/non-functional-requirements.md) |
| Fixed-price scope           | [Template](templates/fixed-price-scope.md)           | [Example](examples/fictional-engagement/fixed-price-scope.md)           |
| Delivery plan               | [Template](templates/delivery-plan.md)               | [Example](examples/fictional-engagement/delivery-plan.md)               |
| Cost model                  | [Template](templates/cost-model.md)                  | [Example](examples/fictional-engagement/cost-model.md)                  |
| Risk register               | [Template](templates/risk-register.md)               | [Example](examples/fictional-engagement/risk-register.md)               |
| Acceptance criteria         | [Template](templates/acceptance-criteria.md)         | [Example](examples/fictional-engagement/acceptance-criteria.md)         |
| Handover checklist          | [Template](templates/handover-checklist.md)          | [Example](examples/fictional-engagement/handover-checklist.md)          |

## Using the templates

Copy the templates into the delivery workspace and replace bracketed fields.
Use stable IDs to connect assumptions, risks, requirements, deliverables and
acceptance evidence. Record owners by role; keep any necessary contact details
in an approved directory, not this repository. Mark exclusions explicitly and
assign unresolved decisions an owner and due date before committing scope.

Start with discovery and the problem statement, agree the smallest valuable
slice, then baseline requirements, scope, cost and delivery. Review risks
throughout. Acceptance needs actual evidence and reviewer approval; handover
needs an operational owner and a verified recovery path.

Keep source documents, personal data, credentials, tokens, connection strings
and sensitive traces out of these artefacts. Link only to access-controlled,
redacted evidence. Runtime scope stays server-resolved from `ExecutionContext`;
these documents do not grant access or approve tool calls. Retrieved text is
untrusted, and any later write tool still needs an args-bound approval.

## Fictional example

The filled set describes **Synthetic Reference Assistant**, a read-only
staging demonstration over fabricated technical reference documents. All
roles, dates, volumes, costs and decisions are invented. There are no real
people, customers, Azure identifiers or source documents.

The example is a coherent **planned engagement**, not a completed deployment.
Acceptance evidence and handover checks are deliberately marked pending rather
than presented as successful tests. Its hypothetical targets and prices are
illustrative, not accelerator benchmarks, Azure quotations or commitments.
