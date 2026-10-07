"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { streamChat } from "@/lib/api/chat";
import type { ChatApproval, ChatCitation } from "@/lib/api/chat";

function citationHref(sourceUri: string): string | null {
  try {
    const parsed = new URL(sourceUri, window.location.origin);
    return parsed.protocol === "https:" || parsed.protocol === "http:"
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

export function ChatPanel() {
  const [message, setMessage] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<ChatCitation[]>([]);
  const [approval, setApproval] = useState<ChatApproval | null>(null);
  const [abstention, setAbstention] = useState<{
    reason: string;
    evidenceIds: string[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const prompt = message.trim();
    if (!prompt || isStreaming) return;

    setAnswer("");
    setCitations([]);
    setApproval(null);
    setAbstention(null);
    setError(null);
    setIsStreaming(true);

    try {
      await streamChat(prompt, (streamEvent) => {
        switch (streamEvent.type) {
          case "token":
            setAnswer((current) => current + streamEvent.text);
            break;
          case "citations":
            setCitations(streamEvent.citations);
            break;
          case "approval":
            setApproval(streamEvent.approval);
            break;
          case "abstention":
            setAbstention({
              reason: streamEvent.reason,
              evidenceIds: streamEvent.evidence_ids,
            });
            break;
          case "error":
            setError(streamEvent.message);
            break;
          case "done":
            break;
        }
      });
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "The chat request failed.",
      );
    } finally {
      setIsStreaming(false);
    }
  }

  return (
    <section aria-labelledby="chat-heading" className="chat">
      <h2 id="chat-heading">Chat</h2>
      <form onSubmit={submit}>
        <label htmlFor="chat-message">Message</label>
        <textarea
          id="chat-message"
          name="message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          required
          rows={3}
          disabled={isStreaming}
        />
        <button type="submit" disabled={isStreaming || !message.trim()}>
          {isStreaming ? "Generating…" : "Send"}
        </button>
      </form>

      {isStreaming && <p role="status">Generating answer…</p>}
      {error && <p role="alert">{error}</p>}
      {answer && (
        <section aria-label="Assistant response" className="chat-response">
          <h3>Answer</h3>
          <p aria-live="polite" data-testid="answer-text">
            {answer}
          </p>
        </section>
      )}
      {citations.length > 0 && (
        <section aria-label="Citations" className="chat-citations">
          <h3>Evidence</h3>
          <ul>
            {citations.map((citation) => {
              const href = citationHref(citation.source_uri);
              return (
                <li key={citation.chunk_id}>
                  {href ? (
                    <a href={href} target="_blank" rel="noreferrer">
                      {citation.document_title} ({citation.chunk_id})
                    </a>
                  ) : (
                    <span>
                      {citation.document_title} ({citation.chunk_id})
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {approval && (
        <section aria-label="Approval required" className="approval-card">
          <h3>Approval required</h3>
          <p>{approval.tool_name} is waiting for approval.</p>
          {approval.expires_at && (
            <p>
              Expires:{" "}
              <time dateTime={approval.expires_at}>{approval.expires_at}</time>
            </p>
          )}
          <p>Approval reference: {approval.approval_id}</p>
        </section>
      )}
      {abstention && (
        <section aria-label="Answer withheld" className="abstention">
          <h3>Answer withheld</h3>
          <p>{abstention.reason}</p>
          {abstention.evidenceIds.length > 0 && (
            <p>Evidence considered: {abstention.evidenceIds.join(", ")}</p>
          )}
        </section>
      )}
    </section>
  );
}
