import fs from "node:fs";
import path from "node:path";
import type { AgentCard } from "./collaboration-loop.js";

export type CollaborationTranscript = {
  version: 1;
  task: string;
  agentCards: AgentCard[];
  collaborationResult: {
    ok?: boolean;
    error?: string;
    answer?: string;
    iterations?: number;
  };
  savedAt: string;
};

export type ResumePlan = {
  priorCards: AgentCard[];
  nextAction: "local" | "helper";
  iteration: number;
  helperContext: string;
  conversationContext: string;
  pendingHelperPrompt?: string;
  retryHelper?: boolean;
};

const TRANSCRIPT_FILE = "collaboration-transcript.json";

export function transcriptPath(projectPath: string, runId: string = "current"): string {
  const root = runId === "current"
    ? path.join(projectPath, ".agent", "current")
    : path.join(projectPath, ".agent", "history", runId);
  return path.join(root, TRANSCRIPT_FILE);
}

export function saveCollaborationTranscript(
  projectPath: string,
  transcript: Omit<CollaborationTranscript, "version" | "savedAt">,
): string {
  const target = transcriptPath(projectPath, "current");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const payload: CollaborationTranscript = {
    version: 1,
    savedAt: new Date().toISOString(),
    ...transcript,
  };
  fs.writeFileSync(target, JSON.stringify(payload, null, 2) + "\n", "utf8");
  return target;
}

export function readCollaborationTranscript(projectPath: string, runId: string): CollaborationTranscript | null {
  const file = transcriptPath(projectPath, runId);
  if (!fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, "utf8")) as CollaborationTranscript;
  } catch {
    return null;
  }
}

/** Cap each entry so prior turns stay cheap — agents get summaries, not transcripts. */
const CONTEXT_ENTRY_MAX = 600;

function clip(text: string, max = CONTEXT_ENTRY_MAX): string {
  const trimmed = text.trim();
  return trimmed.length <= max ? trimmed : `${trimmed.slice(0, max)}…`;
}

export function buildConversationContext(cards: AgentCard[]): string {
  if (!cards.length) return "";
  const lines: string[] = [];
  for (const card of cards) {
    if (card.agent === "user") {
      if (card.outcomeText) lines.push(`### User intervention\n${card.outcomeText.trim()}`, "");
      continue;
    }
    const header = `### ${card.agentLabel} (iteration ${card.iteration}, ${card.status})`;
    lines.push(header);
    if (card.summary) lines.push(`Summary: ${card.summary}`);
    if (card.outcomeType === "answer" && card.outcomeText) {
      lines.push(`Answer: ${clip(card.outcomeText)}`);
    }
    if (card.outcomeType === "prompt" && card.outcomeText) {
      lines.push(`Delegated to helper: ${clip(card.outcomeText)}`);
    }
    if (card.outcomeType === "response" && card.outcomeText) {
      lines.push(`Helper response: ${clip(card.outcomeText)}`);
    }
    for (const msg of card.messages ?? []) {
      if (msg.role === "agent") {
        lines.push(`Decision: ${msg.text}`);
      }
    }
    lines.push("");
  }
  return lines.join("\n").trim();
}

/** Last N completed cards only — for follow-up handoffs without repeating full history. */
export function buildRecentConversationContext(cards: AgentCard[], maxCards = 2): string {
  const done = cards.filter((c) => c.status !== "running");
  if (!done.length) return "";
  return buildConversationContext(done.slice(-maxCards));
}

export function lastHelperResponse(cards: AgentCard[]): string {
  for (let i = cards.length - 1; i >= 0; i--) {
    const card = cards[i];
    if (card.agent === "helper" && card.outcomeType === "response" && card.outcomeText) {
      return card.outcomeText;
    }
  }
  return "";
}

export function canResumeTranscript(transcript: CollaborationTranscript | null): boolean {
  if (!transcript?.agentCards?.length) return false;
  if (transcript.collaborationResult?.ok) return false;
  return true;
}

export function determineResumePlan(transcript: CollaborationTranscript): ResumePlan {
  const cards = transcript.agentCards;
  const conversationContext = buildConversationContext(cards);
  const helperContext = lastHelperResponse(cards);
  const maxIter = Math.max(...cards.map((c) => c.iteration), 0);

  if (!cards.length) {
    return {
      priorCards: [],
      nextAction: "local",
      iteration: 1,
      helperContext: "",
      conversationContext: "",
    };
  }

  const last = cards[cards.length - 1];

  if (last.agent === "helper") {
    if (last.status === "failed") {
      const userMsg = last.messages?.find((m) => m.role === "user");
      return {
        priorCards: cards,
        nextAction: "helper",
        iteration: last.iteration,
        helperContext,
        conversationContext,
        pendingHelperPrompt: userMsg?.text,
        retryHelper: true,
      };
    }
    return {
      priorCards: cards,
      nextAction: "local",
      iteration: maxIter + 1,
      helperContext: last.outcomeText ?? helperContext,
      conversationContext,
    };
  }

  if (last.status === "failed") {
    return {
      priorCards: cards,
      nextAction: "local",
      iteration: last.iteration,
      helperContext,
      conversationContext,
    };
  }

  if (last.outcomeType === "prompt") {
    return {
      priorCards: cards,
      nextAction: "helper",
      iteration: last.iteration,
      helperContext,
      conversationContext,
      pendingHelperPrompt: last.outcomeText,
    };
  }

  return {
    priorCards: cards,
    nextAction: "local",
    iteration: maxIter + 1,
    helperContext,
    conversationContext,
  };
}
