export type WebResearchFact = {
  field?: string;
  value?: string;
  source_url?: string;
  quote?: string;
};

export type WebResearchProgress = {
  step?: string;
  url?: string;
  index?: number;
  total?: number;
  message?: string;
  ts?: string;
};

export type WebResearchController = {
  status?: string;
  phase?: string;
  step?: number;
  max_steps?: number;
  reason?: string;
  [key: string]: unknown;
};

export type WebResearchSnapshot = {
  url?: string;
  title?: string;
  screenshot_b64?: string;
  semantic_snapshot?: string;
  semanticSnapshot?: string;
  text?: string;
  interactables?: import("@/lib/projectTypes").InteractableElement[];
  [key: string]: unknown;
};

export type WebResearchDecision = {
  action?: string;
  target?: string;
  reason?: string;
  confidence?: number;
  [key: string]: unknown;
};

export type WebResearchItem = {
  id?: string;
  url?: string;
  title?: string;
  label?: string;
  text?: string;
  status?: string;
  score?: number;
  [key: string]: unknown;
};

export type WebResearchLlmExchange = {
  seq?: number;
  prompt_key?: string;
  label?: string;
  model?: string;
  session_id?: string;
  step_id?: string;
  snapshot_id?: string;
  url?: string;
  system_prompt?: string;
  user_input?: string;
  response?: string;
  ok?: boolean;
  error?: string;
  truncated?: boolean;
  ts?: string;
  [key: string]: unknown;
};

export type WebResearchState = {
  query?: string;
  answer?: string;
  pages_fetched?: number;
  facts_added?: number;
  errors?: string[];
  facts?: WebResearchFact[];
  progress?: WebResearchProgress | null;
  indexPages?: Array<{ url?: string; title?: string }>;
  liveFacts?: WebResearchFact[];
  controller?: WebResearchController;
  currentUrl?: string;
  snapshot?: WebResearchSnapshot;
  decision?: WebResearchDecision;
  steps?: WebResearchItem[];
  candidates?: WebResearchItem[];
  visitGraph?: {
    nodes?: WebResearchItem[] | Record<string, unknown>;
    edges?: WebResearchItem[];
    [key: string]: unknown;
  };
  evidence?: WebResearchItem[];
  criteria?: WebResearchItem[];
  unmetCriteria?: string[];
  helperExchanges?: WebResearchItem[];
  transitions?: WebResearchItem[];
  formValuePlans?: WebResearchItem[];
  llmExchanges?: WebResearchLlmExchange[];
  runFinished?: boolean;
  updatedAt?: string;
  [key: string]: unknown;
};

const WEB_EVENT_TYPES = new Set([
  "web_research_progress",
  "web_research_result",
  "web_index",
  "web_facts",
  "web_controller_state",
  "web_controller",
  "web_page_snapshot",
  "web_snapshot",
  "web_semantic_snapshot",
  "web_decision",
  "web_agent_decision",
  "web_step",
  "web_candidates",
  "web_visit_graph",
  "web_evidence",
  "web_criteria",
  "web_help_request",
  "web_help_response",
  "web_state_transition",
  "web_form_values_plan",
  "web_llm_exchange",
]);

type UnknownEvent = Record<string, unknown> & { type?: string; ts?: string };

function object(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function objects(value: unknown): WebResearchItem[] | undefined {
  return Array.isArray(value) ? value.filter((item): item is WebResearchItem => Boolean(object(item))) : undefined;
}

function payload(event: UnknownEvent, ...keys: string[]): Record<string, unknown> {
  for (const key of keys) {
    const value = object(event[key]);
    if (value) return value;
  }
  return event;
}

function append(items: WebResearchItem[] | undefined, item: WebResearchItem): WebResearchItem[] {
  return [...(items ?? []), item].slice(-200);
}

export function isWebResearchEvent(event: UnknownEvent): boolean {
  const type = String(event.type ?? "");
  return WEB_EVENT_TYPES.has(type) || type.startsWith("web_");
}

export function applyWebResearchEvent(
  previous: WebResearchState | null,
  event: UnknownEvent,
): WebResearchState | null {
  if (!isWebResearchEvent(event)) return previous;
  const type = String(event.type);
  const ts = event.ts ?? new Date().toISOString();
  const next: WebResearchState = { ...(previous ?? {}), updatedAt: ts };

  if (type === "web_research_progress") {
    next.progress = {
      step: typeof event.step === "string" ? event.step : undefined,
      url: typeof event.url === "string" ? event.url : undefined,
      index: typeof event.index === "number" ? event.index : undefined,
      total: typeof event.total === "number" ? event.total : undefined,
      message: typeof event.message === "string" ? event.message : undefined,
      ts,
    };
    if (next.progress.url) next.currentUrl = next.progress.url;
  } else if (type === "web_research_result") {
    Object.assign(next, event);
    next.progress = null;
    next.runFinished = true;
    if (Array.isArray(event.facts)) next.liveFacts = event.facts as WebResearchFact[];
    next.steps = objects(event.steps) ?? next.steps;
    next.candidates = objects(event.search_results) ?? next.candidates;
    next.unmetCriteria = Array.isArray(event.unmet_criteria)
      ? event.unmet_criteria.map(String)
      : next.unmetCriteria;
    next.helperExchanges = objects(event.helper_history) ?? next.helperExchanges;
    if (Array.isArray(event.llm_exchanges)) {
      next.llmExchanges = event.llm_exchanges as WebResearchLlmExchange[];
    }
  } else if (type === "web_index") {
    const pages = object(event.pages);
    if (pages) {
      next.indexPages = Object.entries(pages).map(([key, raw]) => {
        const page = object(raw);
        return { url: String(page?.url ?? key), title: page?.title ? String(page.title) : undefined };
      });
    }
  } else if (type === "web_facts") {
    next.liveFacts = Array.isArray(event.facts) ? (event.facts as WebResearchFact[]) : [];
  } else if (type === "web_controller_state" || type === "web_controller") {
    const state = payload(event, "controller", "state");
    next.controller = { ...(next.controller ?? {}), ...state };
    const url = state.current_url ?? state.currentUrl ?? state.url;
    if (typeof url === "string") next.currentUrl = url;
  } else if (["web_page_snapshot", "web_snapshot", "web_semantic_snapshot"].includes(type)) {
    const snapshot = payload(event, "snapshot", "page") as WebResearchSnapshot;
    next.snapshot = { ...(next.snapshot ?? {}), ...snapshot };
    if (typeof snapshot.url === "string") next.currentUrl = snapshot.url;
  } else if (type === "web_decision" || type === "web_agent_decision") {
    next.decision = payload(event, "decision") as WebResearchDecision;
  } else if (type === "web_step") {
    next.steps = append(next.steps, payload(event, "step") as WebResearchItem);
  } else if (type === "web_state_transition") {
    next.transitions = append(next.transitions, payload(event, "transition") as WebResearchItem);
  } else if (type === "web_form_values_plan") {
    next.formValuePlans = append(next.formValuePlans, event as WebResearchItem);
  } else if (type === "web_llm_exchange") {
    const exchange = event as WebResearchLlmExchange;
    const existing = next.llmExchanges ?? [];
    const seq = typeof exchange.seq === "number" ? exchange.seq : undefined;
    if (seq && existing.some((item) => item.seq === seq)) {
      next.llmExchanges = existing;
    } else {
      next.llmExchanges = [...existing, exchange].slice(-500);
    }
  } else if (type === "web_candidates") {
    next.candidates = objects(event.candidates ?? event.items) ?? [];
  } else if (type === "web_visit_graph") {
    next.visitGraph = payload(event, "graph", "visit_graph") as WebResearchState["visitGraph"];
  } else if (type === "web_evidence") {
    next.evidence =
      objects(event.evidence ?? event.items) ??
      append(next.evidence, payload(event, "item") as WebResearchItem);
  } else if (type === "web_criteria") {
    next.criteria = objects(event.criteria ?? event.items) ?? next.criteria;
    next.unmetCriteria = Array.isArray(event.unmet_criteria ?? event.unmetCriteria)
      ? ((event.unmet_criteria ?? event.unmetCriteria) as unknown[]).map(String)
      : next.criteria
          ?.filter((item) => item.met === false || item.satisfied === false)
          .map((item) => String(item.criterion ?? item.text ?? item.label ?? ""))
          .filter(Boolean);
  } else if (type === "web_help_request" || type === "web_help_response") {
    next.helperExchanges = append(next.helperExchanges, {
      ...event,
      direction: type === "web_help_request" ? "request" : "response",
    });
  }
  return next;
}
