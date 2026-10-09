"use client";

// Replaces the root layout when it fails, so it renders its own <html>.
export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main>
          <h1>Something went wrong</h1>
          <p role="alert">
            The application could not be displayed.
            {error.digest ? ` Reference: ${error.digest}.` : ""}
          </p>
          <button type="button" onClick={() => retry()}>
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
