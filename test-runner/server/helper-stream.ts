/** Formats live helper-agent SDK events into readable status + draft text. */
export class HelperStreamAggregator {
  private statusLine = "";
  private draftText = "";
  private lastToolLine = "";

  push(event: Record<string, unknown>): void {
    if (event.type === "cursor_activity") {
      const kind = String(event.kind ?? "");
      const activity = String(event.activity ?? "").trim();
      if (!activity) return;
      // Thinking arrives as tiny deltas — never show in the live bubble.
      if (kind === "thinking") return;
      if (kind === "tool") {
        if (activity === this.lastToolLine) return;
        this.lastToolLine = activity;
        this.statusLine = activity;
        return;
      }
      this.statusLine = activity;
      return;
    }

    if (event.type === "cursor" && event.message) {
      const status = String(event.status ?? "");
      let message = String(event.message).trim();
      if (!message) return;
      if (status === "agent_ready") {
        message = "Agent connected";
      } else if (status === "running") {
        message = "Running…";
      }
      if (status === "starting" || status === "agent_ready" || status === "running") {
        this.statusLine = message;
      }
      return;
    }

    if (event.type === "cursor_text" && event.text) {
      this.draftText += String(event.text);
    }
  }

  snapshot(): { streamStatus?: string; streamText?: string } {
    const streamStatus = this.statusLine.trim() || undefined;
    const streamText = this.draftText.trim() || undefined;
    return { streamStatus, streamText };
  }
}
