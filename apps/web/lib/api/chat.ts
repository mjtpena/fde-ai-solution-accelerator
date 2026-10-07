export type ChatCitation = {
  chunk_id: string;
  document_title: string;
  source_uri: string;
};

export type ChatApproval = {
  approval_id: string;
  tool_name: string;
  status: "pending";
  expires_at?: string;
};

export type ChatStreamEvent =
  | { type: "token"; text: string }
  | { type: "citations"; citations: ChatCitation[] }
  | { type: "approval"; approval: ChatApproval }
  | { type: "abstention"; reason: string; evidence_ids: string[] }
  | { type: "done" }
  | { type: "error"; message: string };

type EventHandler = (event: ChatStreamEvent) => void;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isCitation(value: unknown): value is ChatCitation {
  return (
    isRecord(value) &&
    typeof value.chunk_id === "string" &&
    typeof value.document_title === "string" &&
    typeof value.source_uri === "string"
  );
}

function isApproval(value: unknown): value is ChatApproval {
  return (
    isRecord(value) &&
    typeof value.approval_id === "string" &&
    typeof value.tool_name === "string" &&
    value.status === "pending" &&
    (value.expires_at === undefined || typeof value.expires_at === "string")
  );
}

function parseEvent(frame: string): ChatStreamEvent | null {
  let eventName = "";
  const data: string[] = [];

  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const value =
      separator < 0 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") eventName = value;
    if (field === "data") data.push(value);
  }

  if (data.length === 0) return null;
  let value: unknown;
  try {
    value = JSON.parse(data.join("\n"));
  } catch {
    throw new Error("The chat stream returned invalid event data.");
  }

  if (!isRecord(value)) {
    throw new Error("The chat stream returned an invalid event.");
  }

  switch (eventName) {
    case "token":
      if (typeof value.text === "string")
        return { type: "token", text: value.text };
      break;
    case "citations":
      if (Array.isArray(value.citations) && value.citations.every(isCitation)) {
        return { type: "citations", citations: value.citations };
      }
      break;
    case "approval":
      if (isApproval(value.approval))
        return { type: "approval", approval: value.approval };
      break;
    case "abstention":
      if (
        typeof value.reason === "string" &&
        Array.isArray(value.evidence_ids) &&
        value.evidence_ids.every((id) => typeof id === "string")
      ) {
        return {
          type: "abstention",
          reason: value.reason,
          evidence_ids: value.evidence_ids,
        };
      }
      break;
    case "done":
      return { type: "done" };
    case "error":
      if (typeof value.message === "string")
        return { type: "error", message: value.message };
      break;
    default:
      throw new Error(
        `The chat stream returned an unknown event: ${eventName}.`,
      );
  }

  throw new Error(`The chat stream returned an invalid ${eventName} event.`);
}

export async function streamChat(
  message: string,
  onEvent: EventHandler,
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const response = await fetcher("/api/chat/stream", {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message }),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Chat request failed with status ${response.status}.`);
  }
  if (!response.headers.get("content-type")?.includes("text/event-stream")) {
    throw new Error("Chat response was not an event stream.");
  }
  if (!response.body) {
    throw new Error("Chat response did not contain a stream.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  let ended = false;

  const dispatchFrames = (flush: boolean) => {
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = flush ? "" : (frames.pop() ?? "");
    for (const frame of frames) {
      const event = parseEvent(frame);
      if (event) {
        onEvent(event);
        if (event.type === "done") {
          completed = true;
          return;
        }
      }
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      ended = done;
      buffer += decoder.decode(value, { stream: !done });
      dispatchFrames(done);
      if (completed) return;
      if (done) {
        throw new Error("The chat stream ended before completion.");
      }
    }
  } finally {
    try {
      if (!ended) await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}
