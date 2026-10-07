import { getHealth } from "@/lib/api/client";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  let healthStatus: string | null = null;
  let healthError: string | null = null;

  try {
    const health = await getHealth();
    healthStatus = health.status;
  } catch (error) {
    healthError =
      error instanceof Error ? error.message : "The API health check failed.";
  }

  const isAvailable = healthStatus !== null;

  return (
    <main>
      <h1>FDE AI Solution Accelerator</h1>
      <p>Web application baseline.</p>
      <section
        aria-labelledby="health-heading"
        className="health"
        data-state={isAvailable ? "available" : "unavailable"}
      >
        <h2 id="health-heading">API health</h2>
        {isAvailable ? (
          <p role="status">API status: {healthStatus}</p>
        ) : (
          <p role="alert">API unavailable: {healthError}</p>
        )}
      </section>
    </main>
  );
}
