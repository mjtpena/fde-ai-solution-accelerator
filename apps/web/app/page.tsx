import { EntraSignIn } from "@/lib/auth/EntraSignIn";
import { ChatPanel } from "./chat-panel";

export default function HomePage() {
  return (
    <main>
      <h1>FDE AI Solution Accelerator</h1>
      <p>Grounded answers with traceable evidence.</p>
      <EntraSignIn />
      <ChatPanel />
    </main>
  );
}
