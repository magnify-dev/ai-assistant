export type JsonRecord = Record<string, unknown>;

export type WebResearchState = JsonRecord & {
  query?: string;
  answer?: string;
  controller?: JsonRecord;
  currentUrl?: string;
  snapshot?: JsonRecord;
  decision?: JsonRecord;
  steps?: JsonRecord[];
  candidates?: JsonRecord[];
  visitGraph?: JsonRecord;
  evidence?: JsonRecord[];
  extractPreviews?: JsonRecord[];
  criteria?: JsonRecord[];
  unmetCriteria?: string[];
  helperExchanges?: JsonRecord[];
  transitions?: JsonRecord[];
  formValuePlans?: JsonRecord[];
  llmExchanges?: JsonRecord[];
  agentMemory?: JsonRecord[];
  runFinished?: boolean;
  progress?: JsonRecord | null;
  indexPages?: JsonRecord[];
  liveFacts?: JsonRecord[];
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
  "web_extract_preview",
  "web_criteria",
  "web_help_request",
  "web_help_response",
  "web_state_transition",
  "web_form_values_plan",
  "web_llm_exchange",
  "web_agent_memory",
]);

function record(value: unknown): JsonRecord | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : undefined;
}

function records(value: unknown): JsonRecord[] | undefined {
  return Array.isArray(value) ? value.filter((item): item is JsonRecord => Boolean(record(item))) : undefined;
}

function strings(value: unknown): string[] | undefined {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : undefined;
}

function appendBounded(items: JsonRecord[] | undefined, item: JsonRecord, limit = 200): JsonRecord[] {
  const next = [...(items ?? []), item];
  return next.slice(-limit);
}

function eventPayload(event: JsonRecord, ...keys: string[]): JsonRecord {
  for (const key of keys) {
    const value = record(event[key]);
    if (value) return value;
  }
  return event;
}

export function isWebResearchEvent(event: JsonRecord): boolean {
  const type = String(event.type ?? "");
  return WEB_EVENT_TYPES.has(type) || type.startsWith("web_");
}

/** Compose incremental NDJSON events into one reconnect-safe research snapshot. */
export function composeWebResearchState(
  previous: WebResearchState | null | undefined,
  event: JsonRecord,
): WebResearchState | null {
  if (!isWebResearchEvent(event)) return previous ?? null;

  const type = String(event.type ?? "");
  const next: WebResearchState = { ...(previous ?? {}) };
  const ts = typeof event.ts === "string" ? event.ts : new Date().toISOString();

  if (type === "web_research_progress") {
    next.progress = {
      step: event.step,
      url: event.url,
      index: event.index,
      total: event.total,
      message: event.message,
      ts,
    };
    if (typeof event.url === "string" && event.url) next.currentUrl = event.url;
  } else if (type === "web_research_result") {
    Object.assign(next, event);
    next.progress = null;
    next.runFinished = true;
    if (Array.isArray(event.facts)) next.liveFacts = records(event.facts) ?? [];
    next.steps = records(event.steps) ?? next.steps;
    next.candidates = records(event.search_results) ?? next.candidates;
    next.unmetCriteria = strings(event.unmet_criteria) ?? next.unmetCriteria;
    next.helperExchanges = records(event.helper_history) ?? next.helperExchanges;
    if (Array.isArray(event.llm_exchanges)) {
      next.llmExchanges = records(event.llm_exchanges) ?? [];
    }
    if (Array.isArray(event.agent_memory)) {
      next.agentMemory = records(event.agent_memory) ?? [];
    }
  } else if (type === "web_index") {
    const pages = record(event.pages);
    if (pages) {
      next.indexPages = Object.entries(pages).map(([key, value]) => {
        const page = record(value) ?? {};
        return { ...page, url: page.url ?? key };
      });
    }
  } else if (type === "web_facts") {
    next.liveFacts = records(event.facts) ?? [];
  } else if (type === "web_controller_state" || type === "web_controller") {
    const payload = eventPayload(event, "controller", "state");
    next.controller = { ...(next.controller ?? {}), ...payload, ts };
    const url = payload.current_url ?? payload.currentUrl ?? payload.url;
    if (typeof url === "string" && url) next.currentUrl = url;
  } else if (
    type === "web_page_snapshot" ||
    type === "web_snapshot" ||
    type === "web_semantic_snapshot"
  ) {
    const payload = eventPayload(event, "snapshot", "page");
    next.snapshot = { ...(next.snapshot ?? {}), ...payload, ts };
    const url = payload.url ?? event.url;
    if (typeof url === "string" && url) next.currentUrl = url;
  } else if (type === "web_decision" || type === "web_agent_decision") {
    next.decision = { ...eventPayload(event, "decision"), ts };
  } else if (type === "web_step") {
    next.steps = appendBounded(next.steps, { ...eventPayload(event, "step"), ts });
  } else if (type === "web_state_transition") {
    next.transitions = appendBounded(next.transitions, { ...eventPayload(event, "transition"), ts });
  } else if (type === "web_form_values_plan") {
    next.formValuePlans = appendBounded(next.formValuePlans, { ...event, ts });
  } else if (type === "web_llm_exchange") {
    const seq = typeof event.seq === "number" ? event.seq : undefined;
    const existing = next.llmExchanges ?? [];
    if (seq && existing.some((item) => item.seq === seq)) {
      next.llmExchanges = existing;
    } else {
      next.llmExchanges = appendBounded(existing, { ...event, ts }, 500);
    }
  } else if (type === "web_agent_memory") {
    const entry = record(event.entry);
    const batch = records(event.memory);
    if (batch?.length) {
      next.agentMemory = batch;
    } else if (entry) {
      const existing = next.agentMemory ?? [];
      const stepId = String(entry.step_id ?? "");
      const updated = event.updated === true;
      if (updated && stepId && existing.some((item) => String(item.step_id ?? "") === stepId)) {
        next.agentMemory = existing.map((item) =>
          String(item.step_id ?? "") === stepId ? { ...item, ...entry, ts } : item,
        );
      } else if (!stepId || !existing.some((item) => String(item.step_id ?? "") === stepId)) {
        next.agentMemory = appendBounded(existing, { ...entry, ts });
      }
    }
  } else if (type === "web_candidates") {
    next.candidates = records(event.candidates ?? event.items) ?? [];
  } else if (type === "web_visit_graph") {
    next.visitGraph = { ...eventPayload(event, "graph", "visit_graph"), ts };
  } else if (type === "web_evidence") {
    const batch = records(event.evidence ?? event.items);
    next.evidence = batch ?? appendBounded(next.evidence, { ...eventPayload(event, "item"), ts });
  } else if (type === "web_extract_preview") {
    next.extractPreviews = appendBounded(next.extractPreviews, { ...event, ts }, 100);
  } else if (type === "web_criteria") {
    next.criteria = records(event.criteria ?? event.items) ?? next.criteria;
    next.unmetCriteria =
      strings(event.unmet_criteria ?? event.unmetCriteria) ??
      next.criteria
        ?.filter((criterion) => criterion.met === false || criterion.satisfied === false)
        .map((criterion) => String(criterion.criterion ?? criterion.text ?? criterion.label ?? ""))
        .filter(Boolean);
  } else if (type === "web_help_request" || type === "web_help_response") {
    next.helperExchanges = appendBounded(next.helperExchanges, {
      ...event,
      direction: type === "web_help_request" ? "request" : "response",
      ts,
    });
  }

  next.updatedAt = ts;
  return next;
}
