/* @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import RouteError from "../../app/error";
import GlobalError from "../../app/global-error";

const SECRET = "SELECT * FROM users WHERE token='secret-detail'";

function failure(digest?: string): Error & { digest?: string } {
  return Object.assign(new Error(SECRET), digest ? { digest } : {});
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RouteError", () => {
  it("shows only the digest and retries on request", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const retry = vi.fn();
    render(<RouteError error={failure("digest-123")} retry={retry} />);

    expect(screen.getByRole("alert").textContent).toContain(
      "Reference: digest-123.",
    );
    expect(document.body.textContent).not.toContain("secret-detail");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("logs the digest but never the error message", () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<RouteError error={failure("digest-123")} retry={() => {}} />);

    expect(log).toHaveBeenCalledWith("route_error", { digest: "digest-123" });
    expect(JSON.stringify(log.mock.calls)).not.toContain("secret-detail");
  });
});

describe("GlobalError", () => {
  it("renders its own document with a generic message and retries", () => {
    const retry = vi.fn();
    render(<GlobalError error={failure("digest-456")} retry={retry} />);

    expect(document.documentElement.getAttribute("lang")).toBe("en");
    expect(document.body.querySelector("main h1")?.textContent).toBe(
      "Something went wrong",
    );
    expect(screen.getByRole("alert").textContent).toContain(
      "Reference: digest-456.",
    );
    expect(document.body.textContent).not.toContain("secret-detail");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("omits the reference when there is no digest", () => {
    render(<GlobalError error={failure()} retry={() => {}} />);

    expect(screen.getByRole("alert").textContent).toBe(
      "The application could not be displayed.",
    );
  });
});
