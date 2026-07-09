export type AgentCardMessage = {
  role: string;
  text: string;
  ts?: string;
};

export type AgentRunCard = {
  id: string;
  agent: "local" | "helper" | "user";
  agentLabel: string;
  iteration: number;
  status: "running" | "done" | "failed";
  startedAt: string;
  completedAt?: string;
  summary?: string;
  outcomeType?: "answer" | "prompt" | "response" | "note";
  /** Live process text while running; cleared when the card finishes. */
  streamStatus?: string;
  streamText?: string;
  outcomeText?: string;
  messages?: AgentCardMessage[];
  historical?: boolean;
};

export type CollaborationConfig = {
  helperPrompt: string;
  helperModel: string;
  maxTestRetries: number;
  maxIterations: number;
  maxQuestionRounds: number;
  maxInfoRequests: number;
};

export type CollaborationResult = {
  ok?: boolean;
  answer?: string;
  error?: string;
  iterations?: number;
};
