import { createServer } from "node:http";

const server = createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/healthz") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ status: "ok" }));
    return;
  }

  if (request.method !== "POST" || request.url !== "/chat/stream") {
    response.writeHead(404).end();
    return;
  }
  if (!request.headers.authorization?.startsWith("Bearer ")) {
    response.writeHead(401).end();
    return;
  }

  let body = "";
  for await (const chunk of request) body += chunk.toString();
  const payload = JSON.parse(body);
  if (
    typeof payload.message !== "string" ||
    Object.keys(payload).some((key) => key !== "message")
  ) {
    response.writeHead(400).end();
    return;
  }

  response.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  response.flushHeaders();
  response.write('event: token\ndata: {"text":"Streaming"}\n\n');
  setTimeout(() => {
    response.write('event: token\ndata: {"text":" answer."}\n\n');
    response.write(
      'event: citations\ndata: {"citations":[{"chunk_id":"chunk-1","document_title":"Guide","source_uri":"https://docs.example/guide"}]}\n\n',
    );
    response.write(
      'event: approval\ndata: {"approval":{"approval_id":"approval-1","tool_name":"write_record","status":"pending"}}\n\n',
    );
    response.write("event: done\ndata: {}\n\n");
    response.end();
  }, 1_000);
});

server.listen(8100, "127.0.0.1");
