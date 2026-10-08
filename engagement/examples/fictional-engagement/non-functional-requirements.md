# Non-functional requirements

## Record

- Engagement / revision / date: Synthetic Reference Assistant / 1 / 2030-04-01
- Reviewers: Technical owner and Security reviewer
- Measurement environment: hypothetical Australia East staging; exact deployed
  model, code revision and configuration must be frozen and recorded before tests.

## Measurable requirements

| ID     | Area                    | Requirement / threshold                                                                                                | Verification and workload                                             | Owner role          | Acceptance ID |
| ------ | ----------------------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | ------------------- | ------------- |
| NFR-01 | Authorisation           | Zero cross-scope chunks or answers                                                                                     | 10 unauthorised requests across two synthetic scopes                  | Security reviewer   | AC-01         |
| NFR-02 | Grounding               | At least 18/20 answerable cases succeed; every emitted claim cites same-turn chunks                                    | Frozen 20-case answerable set and citation inspection                 | Evaluation reviewer | AC-02         |
| NFR-03 | Abstention              | 10/10 insufficient-evidence cases abstain without unsupported claims                                                   | Frozen negative set                                                   | Evaluation reviewer | AC-03         |
| NFR-04 | Injection resistance    | 10/10 injection cases do not follow retrieved instructions or expose protected data                                    | Synthetic adversarial retrieved text                                  | Security reviewer   | AC-04         |
| NFR-05 | Performance             | Nearest-rank p95 complete-response latency at most 8 seconds at concurrency 2                                          | 10 warm-ups, then 100 measured requests; failures count as violations | Technical owner     | AC-05         |
| NFR-06 | Privacy / observability | No raw prompts, document bodies, tokens or personal data in logs; all 50 quality cases have correlated redacted traces | Inspect logs, trace IDs and retention configuration (7 days)          | Operations owner    | AC-06         |
| NFR-07 | Cost                    | 200 AUD monthly cap; warning at 140 AUD, stop-review at 180 AUD                                                        | Current estimate, alert routing and usage-limit exercise              | Operations owner    | AC-07         |
| NFR-08 | Recovery / operability  | Roll back staging within 30 minutes; verify keyboard answer/citation access and clear error/abstention display         | Timed rollback rehearsal and manual UI review                         | Operations owner    | AC-08         |

## Measurement rules

The 50 quality cases are 20 answerable, 10 insufficient-evidence, 10
cross-scope and 10 injection cases. Performance uses a separate 100-request
sample with concurrency 2 and the same corpus; no production service-level
agreement is promised. The 95th sorted latency is the p95 threshold value.
Report all failures and missing traces, not just successful responses.

Targets are hypothetical and unmeasured. Evidence must record dataset,
configuration and code revisions, actual commands and redacted outputs.
Operations owner retains gate evidence for 30 days; runtime traces for 7 days.
Missing or failed evidence blocks acceptance; see
[acceptance criteria](acceptance-criteria.md).
