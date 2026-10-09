/**
 * Content Security Policy for the web app.
 *
 * Scripts and styles need a per-request nonce (Next.js applies it to its own
 * tags during dynamic rendering). The browser talks only to this origin (the
 * chat proxy) and to the Entra authority for MSAL, including its hidden-iframe
 * silent token renewal.
 */
export function buildContentSecurityPolicy(options: {
  nonce: string;
  authorityOrigin: string;
  development: boolean;
}): string {
  const { nonce, authorityOrigin, development } = options;
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' blob: data:",
    "font-src 'self'",
    `connect-src 'self' ${authorityOrigin}`,
    `frame-src ${authorityOrigin}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  if (!development) directives.push("upgrade-insecure-requests");
  return directives.join("; ");
}

export function entraAuthorityOrigin(): string {
  return new URL(
    process.env.NEXT_PUBLIC_ENTRA_AUTHORITY_HOST ??
      "https://login.microsoftonline.com",
  ).origin;
}

export function createNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}
