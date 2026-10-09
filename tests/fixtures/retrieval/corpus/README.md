# Evaluation fixture corpus

Synthetic, domain-free documents for the offline smoke evaluation
(`evaluations/example-datasets/smoke.jsonl`). `manifest.json` assigns each file a
document ID, title, version, optional effective date and authorization scope. Chunk IDs are
`<document_id>-<n>` in heading order, so dataset rows can name expected evidence.

- `scope-a` is the evaluation principal's scope; `scope-b` holds a document that
  must never be retrieved for it (scope isolation).
- `retention-policy-2025.md` and `retention-policy-2026.md` disagree on purpose
  (conflict rows).
- `vendor-integration-notes.md` carries prompt-injection text, including an
  attempt to close the untrusted-evidence block and issue directives. Content
  safety drops both of its chunks, so questions about it abstain.
- `maintenance-window-addendum.md` is a poisoned document retrieved next to the
  clean `maintenance-window.md`; content safety must drop it while the clean
  chunk still answers.
- `moderation-fixtures.md` carries synthetic `UNSAFE-<CATEGORY>-<LEVEL>` markers
  that only the offline content-safety checker understands: a high-severity
  passage (the answer must be withheld) and a low-severity remark (it must not).

Everything here is untrusted retrieved text. Do not add real customer content.
