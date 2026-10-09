/* @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockMsal, mockScope } = vi.hoisted(() => ({
  mockMsal: {
    initialize: vi.fn(),
    handleRedirectPromise: vi.fn(),
    getActiveAccount: vi.fn(),
    getAllAccounts: vi.fn(),
    setActiveAccount: vi.fn(),
    acquireTokenSilent: vi.fn(),
    acquireTokenPopup: vi.fn(),
    loginPopup: vi.fn(),
    logoutPopup: vi.fn(),
  },
  mockScope: "api://test/access_as_user",
}));

vi.mock("../../lib/auth/msal", () => ({
  getApiScope: () => mockScope,
  getMsalInstance: () => mockMsal,
}));

import { EntraSignIn } from "../../lib/auth/EntraSignIn";
import { AuthenticatedChat } from "../../app/authenticated-chat";

const account = {
  homeAccountId: "home-account",
  localAccountId: "local-account",
  environment: "login.microsoftonline.com",
  tenantId: "tenant-id",
  username: "reader@example.com",
  name: "API Reader",
};

const tokenResult = {
  accessToken: "test-access-token",
  account,
  authority: "https://login.microsoftonline.com/tenant-id",
  uniqueId: "user-id",
  tenantId: "tenant-id",
  scopes: [mockScope],
  idToken: "test-id-token",
  idTokenClaims: {},
  fromCache: true,
  expiresOn: new Date(Date.now() + 60_000),
  tokenType: "Bearer",
};

describe("EntraSignIn", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockMsal.initialize.mockResolvedValue(undefined);
    mockMsal.handleRedirectPromise.mockResolvedValue(null);
    mockMsal.getActiveAccount.mockReturnValue(null);
    mockMsal.getAllAccounts.mockReturnValue([]);
    mockMsal.acquireTokenSilent.mockResolvedValue(tokenResult);
    mockMsal.loginPopup.mockResolvedValue(tokenResult);
    mockMsal.logoutPopup.mockResolvedValue(undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ status: "ok" }), {
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("restores a cached account and checks API health through this origin", async () => {
    mockMsal.getAllAccounts.mockReturnValue([account]);
    render(<EntraSignIn />);

    expect(await screen.findByText("Signed in as API Reader")).toBeTruthy();
    expect(mockMsal.acquireTokenSilent).toHaveBeenCalledWith({
      account,
      scopes: [mockScope],
    });
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/api/health", expect.anything());
    });
    expect(await screen.findByText("API status: ok")).toBeTruthy();
  });

  it("acquires a fresh token silently for every chat request", async () => {
    mockMsal.getAllAccounts.mockReturnValue([account]);
    mockMsal.acquireTokenSilent
      .mockResolvedValueOnce(tokenResult)
      .mockResolvedValueOnce({ ...tokenResult, accessToken: "renewed-token-1" })
      .mockResolvedValueOnce({ ...tokenResult, accessToken: "renewed-token-2" });
    const authorizations: (string | null)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith("/api/chat/stream")) {
          authorizations.push(new Headers(init?.headers).get("Authorization"));
          return new Response("event: done\ndata: {}\n\n", {
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        return new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    render(<AuthenticatedChat />);
    expect(await screen.findByText("Signed in as API Reader")).toBeTruthy();

    for (const [index, question] of ["First", "Second"].entries()) {
      fireEvent.change(screen.getByLabelText("Message"), {
        target: { value: question },
      });
      const send = screen.getByRole("button", { name: "Send" });
      await waitFor(() => expect(send.getAttribute("disabled")).toBeNull());
      fireEvent.click(send);
      await waitFor(() => expect(authorizations).toHaveLength(index + 1));
      // Streaming finished: the button is usable again.
      await waitFor(() => expect(send.getAttribute("disabled")).toBeNull());
    }

    await waitFor(() =>
      expect(authorizations).toEqual([
        "Bearer renewed-token-1",
        "Bearer renewed-token-2",
      ]),
    );
  });

  it("falls back to an interactive popup when silent renewal needs interaction", async () => {
    const { InteractionRequiredAuthError } = await import("@azure/msal-browser");
    const { acquireApiToken } = await import("../../lib/auth/token");
    mockMsal.acquireTokenSilent.mockRejectedValue(
      new InteractionRequiredAuthError("interaction_required", "test-correlation"),
    );
    mockMsal.acquireTokenPopup.mockResolvedValue({
      ...tokenResult,
      accessToken: "interactive-token",
    });

    await expect(
      acquireApiToken(mockMsal as never, account, mockScope),
    ).resolves.toBe("interactive-token");
    expect(mockMsal.acquireTokenPopup).toHaveBeenCalledWith({
      account,
      scopes: [mockScope],
    });
  });

  it("forwards the MSAL-acquired API token with chat requests", async () => {
    mockMsal.getAllAccounts.mockReturnValue([account]);
    let chatRequestInit: RequestInit | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith("/api/chat/stream")) {
          chatRequestInit = init;
          return new Response(
            'event: token\ndata: {"text":"Authenticated answer."}\n\n' +
              'event: citations\ndata: {"citations":[{"chunk_id":"chunk-1","document_title":"Guide","source_uri":"https://docs.example/guide"}]}\n\n' +
              'event: approval\ndata: {"approval":{"approval_id":"approval-1","tool_name":"write_record","status":"pending"}}\n\n' +
              "event: done\ndata: {}\n\n",
            { headers: { "Content-Type": "text/event-stream" } },
          );
        }
        return new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    render(<AuthenticatedChat />);

    expect(await screen.findByText("Signed in as API Reader")).toBeTruthy();
    const message = screen.getByLabelText("Message");
    fireEvent.change(message, { target: { value: "Question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Authenticated answer.")).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "Guide (chunk-1)" })
        .getAttribute("href"),
    ).toBe("https://docs.example/guide");
    expect(
      screen.getByRole("region", { name: "Approval required" }).textContent,
    ).toContain("write_record");
    expect(new Headers(chatRequestInit?.headers).get("Authorization")).toBe(
      "Bearer test-access-token",
    );
    expect(JSON.parse(String(chatRequestInit?.body))).toEqual({
      message: "Question",
    });
  });

  it("does not enable chat before sign-in", async () => {
    render(<AuthenticatedChat />);

    expect(
      await screen.findByText("Sign in to send a chat message."),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Send" }).getAttribute("disabled"),
    ).not.toBeNull();
  });

  it("signs in through the popup and checks the API", async () => {
    render(<EntraSignIn />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Sign in with Microsoft" }),
    );

    expect(await screen.findByText("Signed in as API Reader")).toBeTruthy();
    expect(mockMsal.loginPopup).toHaveBeenCalledWith({ scopes: [mockScope] });
    expect(mockMsal.setActiveAccount).toHaveBeenCalledWith(account);
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/api/health", expect.anything());
    });
  });

  it("shows popup sign-in failures", async () => {
    mockMsal.loginPopup.mockRejectedValue(new Error("Popup blocked."));
    render(<EntraSignIn />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Sign in with Microsoft" }),
    );

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Popup blocked.",
    );
  });

  it("signs out successfully and returns to the sign-in action", async () => {
    mockMsal.getAllAccounts.mockReturnValue([account]);
    render(<EntraSignIn />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(
      await screen.findByRole("button", { name: "Sign in with Microsoft" }),
    ).toBeTruthy();
    expect(mockMsal.logoutPopup).toHaveBeenCalledWith({ account });
  });

  it("keeps the account and reports popup sign-out failures", async () => {
    mockMsal.getAllAccounts.mockReturnValue([account]);
    mockMsal.logoutPopup.mockRejectedValue(new Error("Logout failed."));
    render(<EntraSignIn />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Logout failed.",
    );
    expect(screen.getByText("Signed in as API Reader")).toBeTruthy();
  });
});
