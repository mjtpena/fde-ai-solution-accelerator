import {
  type AccountInfo,
  type IPublicClientApplication,
  InteractionRequiredAuthError,
} from "@azure/msal-browser";

export type AccessTokenProvider = () => Promise<string>;

/**
 * Return a current API access token for `account`.
 *
 * `acquireTokenSilent` serves the cached token while it is valid and renews it
 * (refresh token or hidden iframe) when it is about to expire. Only when Entra
 * requires interaction (consent, MFA, expired session) does it fall back to a
 * popup. Tokens stay in MSAL's session-storage cache, never in app state.
 */
export async function acquireApiToken(
  msal: IPublicClientApplication,
  account: AccountInfo,
  scope: string,
): Promise<string> {
  try {
    const result = await msal.acquireTokenSilent({ account, scopes: [scope] });
    return result.accessToken;
  } catch (error) {
    if (!(error instanceof InteractionRequiredAuthError)) throw error;
    const result = await msal.acquireTokenPopup({ account, scopes: [scope] });
    return result.accessToken;
  }
}
