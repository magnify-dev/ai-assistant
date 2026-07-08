/** API base URL — in dev, talk to Express directly to avoid Vite SSE proxy drops on restart. */
export function apiBase(): string {
  if (import.meta.env.DEV) {
    return "http://127.0.0.1:8767";
  }
  return "";
}

export function apiUrl(path: string): string {
  return `${apiBase()}${path}`;
}

export function eventsUrl(): string {
  return apiUrl("/api/events");
}

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiUrl(path), init);
}
