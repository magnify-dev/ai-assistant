import { ChevronDown, ChevronRight, Map } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

type SiteMapContent = {
  contains?: string[];
  summary?: string;
  features?: string[];
};

type SiteMapPage = {
  path?: string;
  url?: string;
  title?: string;
  features?: string[];
  content?: SiteMapContent;
  last_seen_at?: string;
};

type SiteMapData = {
  pages?: Record<string, SiteMapPage>;
  updated_at?: string;
};

export function SiteMapPanel({ projectPath, compact }: { projectPath: string; compact?: boolean }) {
  const [siteMap, setSiteMap] = useState<SiteMapData | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [lastDecision, setLastDecision] = useState<{ action?: string; reason?: string } | null>(null);

  const loadSiteMap = useCallback(() => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/site-map?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data: { siteMap?: SiteMapData | null }) => {
        if (data.siteMap && typeof data.siteMap === "object") {
          setSiteMap(data.siteMap as SiteMapData);
        }
      })
      .catch(() => {});
  }, [projectPath]);

  useEffect(() => {
    loadSiteMap();
  }, [loadSiteMap]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as
        | { type?: string; pages?: Record<string, SiteMapPage>; action?: string; reason?: string }
        | undefined;
      if (!detail) return;
      if (detail.type === "site_map" && detail.pages) {
        setSiteMap((prev) => ({ ...prev, pages: detail.pages }));
      }
      if (detail.type === "agent_decision") {
        setLastDecision({ action: detail.action, reason: detail.reason });
      }
    };
    window.addEventListener("test-runner-event", handler);
    return () => window.removeEventListener("test-runner-event", handler);
  }, []);

  const pages = siteMap?.pages ?? {};
  const pageEntries = Object.entries(pages).sort(([a], [b]) => a.localeCompare(b));

  return (
    <section className={compact ? "" : "rounded-lg border border-white/10 bg-white/5 p-4"}>
      {!compact ? (
        <>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-white/90">
              <Map className="size-4 text-sky-300" />
              Site map (what lives where)
            </h2>
            <span className="text-xs text-white/50">{pageEntries.length} page(s)</span>
          </div>
          <p className="mb-3 text-xs leading-relaxed text-white/55">
            Semantic catalog of page capabilities — e.g. which pages have which tables or sections.
            Describes structure, not live data values. Saved to{" "}
            <code className="text-white/70">.agent/site-map.yaml</code>.
          </p>
        </>
      ) : null}
      {lastDecision?.action ? (
        <div className="mb-3 rounded border border-violet-500/30 bg-violet-950/20 px-3 py-2 text-xs">
          <span className="font-medium text-violet-200">Last decision:</span>{" "}
          <span className="text-white/80">{lastDecision.action}</span>
          {lastDecision.reason ? (
            <span className="text-white/60"> — {lastDecision.reason}</span>
          ) : null}
        </div>
      ) : null}
      {pageEntries.length === 0 ? (
        <p className="text-xs text-white/45">No pages cataloged yet — run with a task to start exploration.</p>
      ) : (
        <ul className="max-h-64 space-y-2 overflow-y-auto text-xs">
          {pageEntries.map(([path, info]) => {
            const open = expanded[path] ?? path === pageEntries[0]?.[0];
            const content = info.content ?? {};
            const contains = content.contains ?? [];
            const summary = content.summary || "";
            const features = info.features ?? content.features ?? [];
            return (
              <li key={path} className="rounded border border-white/5 bg-black/20">
                <button
                  type="button"
                  className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-white/5"
                  onClick={() => setExpanded((prev) => ({ ...prev, [path]: !open }))}
                >
                  {open ? (
                    <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-white/40" />
                  ) : (
                    <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-white/40" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-white/85">{path}</div>
                    <div className="truncate text-white/50">{summary || info.title || ""}</div>
                  </div>
                  <span className="shrink-0 text-white/40">{contains.length} item(s)</span>
                </button>
                {open ? (
                  <div className="border-t border-white/5 px-3 py-2 text-white/60">
                    {features.length > 0 ? (
                      <p className="mb-1 text-white/50">
                        <span className="text-white/40">has:</span> {features.join(", ")}
                      </p>
                    ) : null}
                    {contains.length > 0 ? (
                      <ul className="list-disc space-y-0.5 pl-4 text-white/70">
                        {contains.map((item, i) => (
                          <li key={i}>{item}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-white/45">No capability descriptions yet.</p>
                    )}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
