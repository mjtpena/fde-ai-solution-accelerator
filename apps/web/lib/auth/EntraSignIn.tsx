"use client";

import {
  type AccountInfo,
  type AuthenticationResult,
  InteractionRequiredAuthError,
} from "@azure/msal-browser";
import { useCallback, useEffect, useState } from "react";

import { getHealth } from "../api/client";
import { getApiScope, getMsalInstance } from "./msal";

export function EntraSignIn() {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [healthStatus, setHealthStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const scope = getApiScope();

  useEffect(() => {
    let active = true;

    async function initialize(): Promise<void> {
      const msal = getMsalInstance();
      if (!msal || !scope) {
        setError("Entra sign-in is not configured for this deployment.");
        setReady(true);
        return;
      }

      try {
        await msal.initialize();
        const redirectResult = await msal.handleRedirectPromise();
        const signedInAccount =
          redirectResult?.account ??
          msal.getActiveAccount() ??
          msal.getAllAccounts()[0] ??
          null;
        if (signedInAccount) {
          msal.setActiveAccount(signedInAccount);
          const tokenResult =
            redirectResult?.accessToken
              ? redirectResult
              : await msal.acquireTokenSilent({
                  account: signedInAccount,
                  scopes: [scope],
                });
          if (active) {
            setAccount(signedInAccount);
            setAccessToken(tokenResult.accessToken);
          }
        }
      } catch (caught) {
        if (active) {
          setError(errorMessage(caught));
        }
      } finally {
        if (active) {
          setReady(true);
        }
      }
    }

    void initialize();
    return () => {
      active = false;
    };
  }, [scope]);

  useEffect(() => {
    if (!accessToken) {
      return;
    }

    let active = true;
    void getHealth(accessToken)
      .then((health) => {
        if (active) {
          setHealthStatus(health.status);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(errorMessage(caught));
        }
      });

    return () => {
      active = false;
    };
  }, [accessToken]);

  const signIn = useCallback(async () => {
    const msal = getMsalInstance();
    if (!msal || !scope) {
      setError("Entra sign-in is not configured for this deployment.");
      return;
    }

    try {
      const result: AuthenticationResult = await msal.loginPopup({
        scopes: [scope],
      });
      msal.setActiveAccount(result.account);
      setAccount(result.account);
      setAccessToken(result.accessToken);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [scope]);

  const signOut = useCallback(async () => {
    const msal = getMsalInstance();
    if (!msal || !account) {
      return;
    }

    try {
      await msal.logoutPopup({ account });
      setAccount(null);
      setAccessToken(null);
      setHealthStatus(null);
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [account]);

  return (
    <section aria-labelledby="sign-in-heading">
      <h2 id="sign-in-heading">Microsoft Entra ID</h2>
      {!ready ? (
        <p role="status">Checking sign-in status…</p>
      ) : account ? (
        <div>
          <p role="status">Signed in as {account.name ?? account.username}</p>
          <button onClick={() => void signOut()} type="button">
            Sign out
          </button>
          <p aria-live="polite">
            {healthStatus ? `API status: ${healthStatus}` : "Checking API access…"}
          </p>
        </div>
      ) : (
        <button onClick={() => void signIn()} type="button">
          Sign in with Microsoft
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof InteractionRequiredAuthError) {
    return "Sign in again to grant access to the API.";
  }
  return error instanceof Error ? error.message : "Authentication failed.";
}
