import { AuthenticatedChat } from "./authenticated-chat";

export default function HomePage() {
  return (
    <main>
      <h1>FDE AI Solution Accelerator</h1>
      <p>Grounded answers with traceable evidence.</p>
      <AuthenticatedChat />
    </main>
  );
}
