# Evaluation fixture corpus

Synthetic, domain-free documents for the offline smoke evaluation
(`evaluations/example-datasets/smoke.jsonl`). `manifest.json` assigns each file a
document ID, title, version and authorization scope. Chunk IDs are
`<document_id>-<n>` in heading order, so dataset rows can name expected evidence.

- `scope-a` is the evaluation principal's scope; `scope-b` holds a document that
  must never be retrieved for it (scope isolation).
- `retention-policy-2025.md` and `retention-policy-2026.md` disagree on purpose
  (conflict rows).
- `vendor-integration-notes.md` carries prompt-injection text, including an
  attempt to close the untrusted-evidence block and issue directives.

Everything here is untrusted retrieved text. Do not add real customer content.
