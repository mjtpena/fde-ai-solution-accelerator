# Handover checklist

## Transfer record

- Engagement / release reference / environment: Synthetic Reference Assistant /
  not yet released / hypothetical staging
- Delivering role / receiving role: Technical owner / Operations owner
- Support window: five working days after actual sign-off, baseline defects only
- Escalation route: restricted delivery channel to Technical owner; safety issues
  go immediately to Security reviewer and Operations owner
- Acceptance prerequisite: all eight gates in [acceptance](acceptance-criteria.md)
- Status: planned transfer; no check is represented as completed

## Readiness evidence

- [ ] Scope and exclusions reviewed; all eight acceptance gates approved.
- [ ] Architecture, dependencies and deployed model/configuration revisions recorded.
- [ ] Synthetic corpus, two scopes, retention and deletion procedure verified.
- [ ] Managed identity, RBAC and access owners reviewed; no secrets transferred.
- [ ] Staging deployment, rollback and restore rehearsed within 30 minutes.
- [ ] Correlated redacted traces, audit access and alerts verified.
- [ ] 200 AUD cap, 140/180 AUD thresholds and usage-stop procedure transferred.
- [ ] Frozen quality set and exact rerun commands stored with actual results.
- [ ] Incident stop and restart authority rehearsed.
- [ ] R-01 through R-05 reviewed; residual risks and actions assigned.
- [ ] Operations owner independently demonstrates operation and recovery.
- [ ] Temporary delivery access removed or assigned an approved expiry.

## Evidence and outstanding actions

| Item                                                             | Evidence location / result                             | Owner role          | Due date   | Status  |
| ---------------------------------------------------------------- | ------------------------------------------------------ | ------------------- | ---------- | ------- |
| Scope, quality baseline and acceptance                           | Pending AC-01 to AC-06 evidence pack                   | Evaluation reviewer | 2030-04-09 | Pending |
| Configuration, identities, data lifecycle and telemetry          | Pending redacted configuration and retention review    | Technical owner     | 2030-04-10 | Pending |
| Budget and usage-stop ownership                                  | Pending AC-07 report                                   | Operations owner    | 2030-04-10 | Pending |
| Deployment, rollback, restore, incident and independent recovery | Pending AC-08 timed rehearsal                          | Operations owner    | 2030-04-11 | Pending |
| Residual risks and temporary access                              | Pending risk review and access expiry/removal evidence | Security reviewer   | 2030-04-11 | Pending |

## Sign-off

- Receiving role / decision / date / evidence: Operations owner / pending /
  not signed / no evidence yet
- Delivering role / decision / date / evidence: Technical owner / pending /
  not signed / no evidence yet
- Remaining support / exclusions: correction window starts only on actual
  sign-off; production, enhancements and ongoing operation are excluded.

Do not claim readiness or start the correction window until mandatory gates and
recovery evidence are verified. Day 10 is the retest reserve if a gate fails;
otherwise Sponsor must rebaseline rather than silently accept an incomplete
handover. References: [delivery](delivery-plan.md), [risks](risk-register.md).
