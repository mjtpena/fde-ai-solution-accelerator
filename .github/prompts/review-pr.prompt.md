---
mode: ask
description: Principal-engineer review of the current changes
---
Review the current changes as a principal engineer. Check, in order:
1. Scope isolation — can any path widen scope from user input?
2. Approval bypass — can any write execute without an args-bound approval?
3. Prompt injection — is retrieved text ever treated as instructions?
4. Citations — are unvalidated citations possible?
5. Secrets/telemetry — any secrets or unredacted content logged?
6. Tests — does each behaviour change have a test? Any skipped tests?
7. Simplicity — anything that could be removed?
Output a table: finding · severity · file:line · fix.
