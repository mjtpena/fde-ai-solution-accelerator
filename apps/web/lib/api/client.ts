import type { paths } from "./schema";

type HealthResponse =
  paths["/healthz"]["get"]["responses"][200]["content"]["application/json"];

function isHealthResponse(value: unknown): value is HealthResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "status" in value &&
    (value.status === "ok" || value.status === "ready")
  );
}

export async function getHealth(fetcher: typeof fetch = fetch): Promise<HealthResponse> {
  const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
  const response = await fetcher(new URL("/healthz", apiBaseUrl), {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`API health check failed with status ${response.status}.`);
  }

  const body: unknown = await response.json();
  if (!isHealthResponse(body)) {
    throw new Error("API health check returned an invalid response.");
  }

  return body;
}
