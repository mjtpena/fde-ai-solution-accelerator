"use client";

import { useCallback, useState } from "react";

import { EntraSignIn } from "../lib/auth/EntraSignIn";
import { ChatPanel } from "./chat-panel";

export function AuthenticatedChat() {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const handleAccessTokenChange = useCallback((token: string | null) => {
    setAccessToken(token);
  }, []);

  return (
    <>
      <EntraSignIn onAccessTokenChange={handleAccessTokenChange} />
      <ChatPanel accessToken={accessToken} />
    </>
  );
}
