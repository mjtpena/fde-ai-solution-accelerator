import { describe, expect, it } from "vitest";
import { streamChat } from "../../lib/api/chat";

describe("streamChat", () => {
  it("parses fragmented SSE events and sends only the user message", async () => {
    let requestUrl: string | undefined;
    let requestInit: RequestInit | undefined;
    const frames = [
      'event: token\ndata: {"text":"A grounded answer."}\n\n',
      'event: citations\ndata: {"citations":[{"chunk_id":"chunk-1","document_title":"Guide","source_uri":"https://docs.example/guide"}]}\n\n',
      "event: done\ndata: {}\n\n",
    ];
    const encoded = new TextEncoder().encode(frames.join(""));
    const fragments = [
      encoded.slice(0, 15),
      encoded.slice(15, 51),
      encoded.slice(51),
    ];
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      requestInit = init;
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            for (const fragment of fragments) controller.enqueue(fragment);
            controller.close();
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    };
    const events: unknown[] = [];

    await streamChat(
      "What does the guide say?",
      (event) => events.push(event),
      undefined,
      fetcher,
    );

    expect(requestUrl).toBe("/api/chat/stream");
    expect(requestInit?.method).toBe("POST");
    expect(JSON.parse(String(requestInit?.body))).toEqual({
      message: "What does the guide say?",
    });
    expect(events).toEqual([
      { type: "token", text: "A grounded answer." },
      {
        type: "citations",
        citations: [
          {
            chunk_id: "chunk-1",
            document_title: "Guide",
            source_uri: "https://docs.example/guide",
          },
        ],
      },
      { type: "done" },
    ]);
  });
});
