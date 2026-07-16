export type WebCaptureLocatorStatus = "unique" | "ambiguous" | "unresolved" | "synthetic";

export type WebCaptureRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type WebCaptureLocator = {
  kind: string;
  value: string;
  role?: string;
  name?: string;
  count?: number;
  frame_index?: number;
  frame_url?: string;
};

export type WebCaptureElement = {
  id: string;
  index: number;
  kind: string;
  role?: string | null;
  tag?: string | null;
  text?: string | null;
  aria?: string | null;
  label?: string | null;
  name?: string | null;
  frame_index?: number;
  frame_url?: string | null;
  disabled?: boolean;
  rect: WebCaptureRect;
  locator_status: WebCaptureLocatorStatus;
  locator?: WebCaptureLocator | null;
  locator_candidates?: WebCaptureLocator[];
  ai_interactive?: boolean | null;
  ai_confidence?: number | null;
  ai_control_type?: string | null;
  ai_reason?: string | null;
  deterministic_issues?: string[];
  user_interactive?: boolean | null;
  effective_interactive?: boolean | null;
  map_matched?: boolean;
  map_signature?: string;
  map_corrected_at?: string;
};

export type WebCaptureMapInfo = {
  status: "none" | "missing" | "applied";
  site_key?: string;
  matched?: number;
  saved_entries?: number;
  user_kept?: number;
  user_rejected?: number;
  updated_at?: string;
};

export type WebCaptureVisual = {
  status: "missing" | "built" | "reused" | "unavailable";
  site_key?: string;
  cols: number;
  rows: number;
  cells: string[];
  overlay?: Array<string | null>;
  display_cells: string[];
  built_at?: string;
  corrected_at?: string;
  active_source?: "none" | "built" | "corrected";
};

export type WebCapture = {
  version: number;
  capture_id: string;
  fingerprint: string;
  created_at: string;
  url: string;
  title?: string;
  context?: string;
  viewport: {
    width: number;
    height: number;
    scroll_x: number;
    scroll_y: number;
    document_width: number;
    document_height: number;
  };
  elements: WebCaptureElement[];
  summary: {
    raw: number;
    visible: number;
    unique: number;
    ambiguous: number;
    unresolved: number;
    ai_kept: number;
    ai_rejected: number;
    user_kept?: number;
    user_rejected?: number;
    map_matched?: number;
  };
  map?: WebCaptureMapInfo;
  visual?: WebCaptureVisual;
  ai: {
    status: "pending" | "ready" | "unavailable" | "disabled";
    model?: string;
    cached?: boolean;
    duration_ms?: number;
    error?: string;
  };
};

export type WebCaptureReview = {
  captureId: string;
  verdict: "good" | "needs_work" | "element_correction";
  elementId?: string;
  correctedInteractive?: boolean;
  note?: string;
  ts?: string;
};

export type WebCaptureBuildPhase =
  | "idle"
  | "geometry"
  | "locators"
  | "analyzing"
  | "visual"
  | "complete"
  | "error";

export type WebCaptureBuildStatus = {
  phase: WebCaptureBuildPhase;
  url?: string;
  message?: string;
  error?: string;
  elementCount?: number;
  updatedAt?: string;
};
