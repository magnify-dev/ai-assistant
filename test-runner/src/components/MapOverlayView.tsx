import { cn } from "@/lib/utils";
import type { WebCapture, WebCaptureElement, WebCaptureScrollSlice } from "@/lib/webCaptureTypes";
import {
  boxTone,
  captureBoxStyle,
  captureCanvasHeight,
  captureUsesDocumentCoords,
} from "@/lib/webCaptureView";

type Props = {
  capture: WebCapture;
  elements: WebCaptureElement[];
  screenshotSrc?: string;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  interactive?: boolean;
  className?: string;
  /** Optional project path so relative slice screenshots can be resolved live */
  projectPath?: string;
  updatingMap?: boolean;
  updatingMapMessage?: string;
};

function elementLabel(element: WebCaptureElement): string {
  return (
    element.text?.trim() ||
    element.aria?.trim() ||
    element.label?.trim() ||
    element.name?.trim() ||
    element.kind
  ).slice(0, 48);
}

function resolveSliceSrc(
  capture: WebCapture,
  slice: WebCaptureScrollSlice,
  fallback?: string,
  projectPath?: string,
): string | undefined {
  if (slice.screenshotUrl) return slice.screenshotUrl;
  const rel = slice.screenshot || "";
  if (rel.startsWith("http") || rel.startsWith("data:")) return rel;
  if (rel.startsWith("/") && !rel.startsWith("/api/")) {
    // absolute site path — not a local file
  } else if (rel && projectPath) {
    return `/api/project/web-capture/screenshot?path=${encodeURIComponent(projectPath)}&file=${encodeURIComponent(rel)}`;
  } else if (rel && capture.screenshotUrl?.includes("file=")) {
    // Reuse the query shape from the main screenshot URL, swap the file=.
    try {
      const url = new URL(capture.screenshotUrl, "http://local");
      url.searchParams.set("file", rel);
      return `${url.pathname}?${url.searchParams.toString()}`;
    } catch {
      /* fall through */
    }
  }
  if (!slice.screenshot && capture.screenshotUrl) return capture.screenshotUrl;
  return fallback;
}

export function MapOverlayView({
  capture,
  elements,
  screenshotSrc,
  selectedId,
  onSelect,
  interactive = true,
  className,
  projectPath,
  updatingMap = false,
  updatingMapMessage,
}: Props) {
  const canvasWidth = Math.max(1, capture.viewport.width);
  const canvasHeight = captureCanvasHeight(capture);
  const scrollMap = capture.scroll_map;
  const documentCoords = captureUsesDocumentCoords(capture);
  const tall = canvasHeight > capture.viewport.height * 1.05;
  const liveScreenshot = Boolean(screenshotSrc?.startsWith("data:image/"));
  const useScrollSlices =
    Boolean(scrollMap?.slices?.length && documentCoords) && !liveScreenshot;
  const hasImage = Boolean(
    screenshotSrc || scrollMap?.slices?.some((slice) => slice.screenshot || slice.screenshotUrl),
  );

  return (
    <div className={cn("rounded-lg border border-white/15 bg-neutral-900 p-2", className)}>
      <div
        className={cn(
          "relative mx-auto w-full rounded shadow-inner scrollbar-thin",
          tall ? "max-h-[640px] overflow-y-auto overflow-x-hidden" : "max-h-[520px] overflow-hidden",
          hasImage ? "bg-neutral-950" : "bg-white",
        )}
      >
        {updatingMap && hasImage ? (
          <div className="pointer-events-none absolute left-3 top-3 z-30 flex items-center gap-1.5 rounded-md border border-sky-400/40 bg-black/75 px-2 py-1 text-[10px] font-medium text-sky-100 shadow-lg backdrop-blur-sm">
            <span className="inline-flex h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-200/30 border-t-sky-100" />
            {updatingMapMessage ?? "Updating map…"}
          </div>
        ) : null}
        <div
          className="relative mx-auto w-full"
          style={{ aspectRatio: `${canvasWidth} / ${canvasHeight}` }}
          aria-label="Page screenshot with interaction map overlay"
        >
          {useScrollSlices ? (
            scrollMap!.slices!.map((slice, index) => {
              const src = resolveSliceSrc(capture, slice, screenshotSrc, projectPath);
              if (!src) return null;
              return (
                <img
                  key={`${slice.scroll_y}-${index}`}
                  src={src}
                  alt=""
                  aria-hidden
                  className="absolute left-0 w-full object-cover object-left-top"
                  style={{
                    top: `${(slice.scroll_y / canvasHeight) * 100}%`,
                    height: `${(slice.height / canvasHeight) * 100}%`,
                  }}
                  draggable={false}
                />
              );
            })
          ) : hasImage && screenshotSrc ? (
            <img
              key={screenshotSrc.slice(0, 80)}
              src={screenshotSrc}
              alt=""
              aria-hidden
              className="absolute inset-0 h-full w-full object-cover object-top"
              draggable={false}
            />
          ) : null}

          {documentCoords && scrollMap?.slices && scrollMap.slice_count > 1
            ? scrollMap.slices.slice(1).map((slice, index) => (
                <div
                  key={`slice-line-${slice.scroll_y}-${index}`}
                  className="pointer-events-none absolute left-0 z-10 w-full border-t border-dashed border-white/20"
                  style={{ top: `${(slice.scroll_y / canvasHeight) * 100}%` }}
                />
              ))
            : null}

          {elements.map((element) => {
            const selected = selectedId === element.id;
            const shared = cn(
              "absolute overflow-hidden border text-left text-[9px] leading-tight transition",
              boxTone(element),
              hasImage ? "bg-black/10 backdrop-blur-[1px]" : "",
              selected && "z-20 ring-2 ring-violet-600 ring-offset-1",
            );
            const style = captureBoxStyle(element, capture);
            const title = `${elementLabel(element)} · ${element.locator_status}`;

            if (interactive && onSelect) {
              return (
                <button
                  key={element.id}
                  type="button"
                  title={title}
                  aria-label={`Inspect ${elementLabel(element)}`}
                  onClick={() => onSelect(element.id)}
                  className={shared}
                  style={style}
                >
                  <span className="block truncate px-0.5">{elementLabel(element)}</span>
                </button>
              );
            }

            return (
              <div
                key={element.id}
                title={title}
                className={cn(shared, "pointer-events-none opacity-70")}
                style={style}
              />
            );
          })}
          {!elements.length ? (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-neutral-400">
              No elements match this filter
            </div>
          ) : null}
        </div>
      </div>
      {documentCoords && (scrollMap?.slice_count ?? 0) > 1 ? (
        <p className="mt-2 text-[10px] text-white/45">
          Stitched map · {scrollMap?.slice_count ?? 0} scroll views ·{" "}
          {Math.round(canvasHeight)}px tall
          {scrollMap?.persistent_skipped
            ? ` · ${scrollMap.persistent_skipped} sticky duplicate(s) hidden`
            : ""}
        </p>
      ) : tall ? (
        <p className="mt-2 text-[10px] text-white/45">
          Map · {Math.round(canvasWidth)} × {Math.round(canvasHeight)}px
        </p>
      ) : null}
      {!hasImage ? (
        <p className="mt-2 text-[10px] text-white/40">
          No saved page image yet — run browser capture again to overlay the map on the live screenshot.
        </p>
      ) : null}
    </div>
  );
}
