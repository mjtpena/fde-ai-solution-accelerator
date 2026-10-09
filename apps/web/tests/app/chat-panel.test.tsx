/* @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatPanel } from "../../app/chat-panel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ChatPanel", () => {
  it("removes streamed text and citations when the answer is withdrawn", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            'event: token\ndata: {"text":"Unverified claim."}\n\n' +
              'event: abstention\ndata: {"reason":"The generated answer could not be verified.","evidence_ids":[]}\n\n' +
              "event: done\ndata: {}\n\n",
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );
    render(<ChatPanel accessToken="token" />);

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Question" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(
      (await screen.findByRole("region", { name: "Answer withheld" }))
        .textContent,
    ).toContain("could not be verified");
    await waitFor(() =>
      expect(screen.queryByText("Unverified claim.")).toBeNull(),
    );
    expect(screen.queryByRole("region", { name: "Citations" })).toBeNull();
  });
});
