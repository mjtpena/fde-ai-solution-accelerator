import { forwardChatRequest } from "@/lib/api/chat-proxy";

export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  return forwardChatRequest(request);
}
