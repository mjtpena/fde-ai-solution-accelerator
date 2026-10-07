# Copilot setup guide

## 1. Copy the kit into the repo root
Copy `.github/`, `.vscode/`, `AGENTS.md` and `issues/` into the repository. Move the big spec to `docs/spec.md`.

## 2. Enable Copilot features
- Repo **Settings → Copilot → Coding agent**: enable for this repository.
- Coding agent **MCP configuration** (repo settings): add the Microsoft Learn MCP server so the agent can check current Agent Framework / Foundry APIs:
  ```json
  { "mcpServers": { "microsoft-learn": { "type": "http", "url": "https://learn.microsoft.com/api/mcp", "tools": ["*"] } } }
  ```
- VS Code: `.vscode/mcp.json` is included for agent mode.
- Run the **Copilot Setup Steps** workflow once manually to confirm it goes green.

## 3. Create labels and milestones, then the issues
```bash
gh auth login
./issues/create-issues.sh
```

## 4. Work order
1. **M1 by hand in VS Code agent mode** (`/implement-issue`). Copilot copies existing patterns, so your scaffold sets the standard.
2. From M2 onwards, assign issues to **Copilot** one at a time. Review each PR with `/review-pr`.
3. Keep issues marked `security-sensitive` in VS Code agent mode with you in the loop.
4. One issue per PR. Never more than two in flight.

## 5. Model choice
- Architecture, agent workflows, security-sensitive issues: strongest reasoning model available.
- UI, boilerplate, docs, tests: faster model.

## 6. When Copilot goes wrong
- Comment on the PR with the specific failing criterion — the coding agent iterates on review comments.
- If it drifts twice, close the PR, tighten the issue's "files in scope", and reassign.
- Add the lesson as a rule in the relevant `.instructions.md` so it doesn't recur.

> Verify file names/features against current GitHub Copilot docs; Copilot customisation evolves quickly.
