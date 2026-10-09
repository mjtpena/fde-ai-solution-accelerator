"use client";

import { useEffect } from "react";

// Route-level boundary: shows a generic message and never the error's text,
// which can contain request or server details.
export default function RouteError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error("route_error", { digest: error.digest });
  }, [error]);

  return (
    <main>
      <h1>Something went wrong</h1>
      <p role="alert">
        The page could not be displayed.
        {error.digest ? ` Reference: ${error.digest}.` : ""}
      </p>
      <button type="button" onClick={() => retry()}>
        Try again
      </button>
    </main>
  );
}
