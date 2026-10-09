"use client";

import { useCallback, useState } from "react";

import { EntraSignIn } from "../lib/auth/EntraSignIn";
import type { AccessTokenProvider } from "../lib/auth/token";
import { ChatPanel } from "./chat-panel";

export function AuthenticatedChat() {
  const [getAccessToken, setGetAccessToken] =
    useState<AccessTokenProvider | null>(null);
  const handleTokenProviderChange = useCallback(
    (provider: AccessTokenProvider | null) => {
      // A function value must be wrapped, or React treats it as an updater.
      setGetAccessToken(() => provider);
    },
    [],
  );

  return (
    <>
      <EntraSignIn onTokenProviderChange={handleTokenProviderChange} />
      <ChatPanel getAccessToken={getAccessToken} />
    </>
  );
}
