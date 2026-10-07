"use client";

import {
  BrowserCacheLocation,
  PublicClientApplication,
  type Configuration,
} from "@azure/msal-browser";

export function createMsalConfiguration(
  clientId: string,
  tenantId: string,
): Configuration {
  return {
    auth: {
      clientId,
      authority: `https://login.microsoftonline.com/${tenantId}`,
    },
    cache: {
      cacheLocation: BrowserCacheLocation.SessionStorage,
    },
  };
}

let msalInstance: PublicClientApplication | null = null;

export function getMsalInstance(): PublicClientApplication | null {
  const clientId = process.env.NEXT_PUBLIC_ENTRA_CLIENT_ID;
  const tenantId = process.env.NEXT_PUBLIC_ENTRA_TENANT_ID;
  if (!clientId || !tenantId) {
    return null;
  }

  msalInstance ??= new PublicClientApplication(
    createMsalConfiguration(clientId, tenantId),
  );
  return msalInstance;
}

export function getApiScope(): string | null {
  return process.env.NEXT_PUBLIC_ENTRA_API_SCOPE ?? null;
}
