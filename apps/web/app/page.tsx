import { connection } from "next/server";

import { AuthenticatedChat } from "./authenticated-chat";

export default async function HomePage() {
  // Render per request so Next.js can apply the CSP nonce from proxy.ts.
  await connection();
  return (
    <main>
      <h1>FDE AI Solution Accelerator</h1>
      <p>Grounded answers with traceable evidence.</p>
      <AuthenticatedChat />
    </main>
  );
}
