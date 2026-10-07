---
applyTo: "apps/web/**"
---
# TypeScript / Next.js rules
- Next.js App Router, TypeScript `strict`, no `any`.
- Call the backend only through the generated typed API client in `lib/api`. Never call Azure services from the browser.
- Auth via MSAL; never store tokens in localStorage.
- Server Components by default; Client Components only for interactivity (chat, approvals).
- Stream chat via SSE; render citations as clickable evidence, never as raw IDs only.
- Accessibility: labelled controls, keyboard navigation, visible focus.
- Tests: Vitest + React Testing Library for components; Playwright for critical flows.
