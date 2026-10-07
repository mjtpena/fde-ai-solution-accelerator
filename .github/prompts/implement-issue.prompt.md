---
mode: agent
description: Implement a GitHub issue end to end with tests
---
Implement GitHub issue #${input:issue:Issue number}.

1. Read the issue, its acceptance criteria and the files in scope. Summarise the current design in 3–5 bullets.
2. Propose the smallest plan that meets every acceptance criterion. List files you will change.
3. Implement. Add or update tests for every behaviour change.
4. Run `make check`, then `make eval-smoke`. Fix failures; do not skip tests.
5. Update docs/ADRs if architecture changed.
6. Output: files changed, how each acceptance criterion is met, remaining risks.

Stop and ask if any business rule is ambiguous.
