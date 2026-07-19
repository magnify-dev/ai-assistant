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
  const base = (
    element.text?.trim() ||
    element.title?.trim() ||
    element.aria?.trim() ||
    element.label?.trim() ||
    element.name?.trim() ||
    element.kind
  ).slice(0, 40);
  const date = element.dates?.find((value) => value?.trim())?.trim();
  if (date && !base.toLowerCase().includes(date.toLowerCase())) {
    return `${base} · ${date}`.slice(0, 52);
  }
  return base;
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
  const fullPage = scrollMap?.mode === "full_page" || documentCoords || tall;
  const liveScreenshot = Boolean(screenshotSrc?.startsWith("data:image/"));
  // full_page mode stores one tall slice — draw that (or multi-slice stitch).
  // Never use a live viewport JPEG on a document-tall canvas (squeezes the page).
  const useScrollSlices = Boolean(
    scrollMap?.slices?.length &&
      documentCoords &&
      (scrollMap.mode === "full_page" || (scrollMap.slice_count ?? 0) > 1) &&
      scrollMap.slices.some((slice) => slice.screenshot || slice.screenshotUrl) &&
      !liveScreenshot,
  );
  const hasImage = Boolean(
    useScrollSlices ||
      (!liveScreenshot && screenshotSrc) ||
      (!liveScreenshot &&
        scrollMap?.slices?.some((slice) => slice.screenshot || slice.screenshotUrl)),
  );

  return (
    <div className={cn("rounded-lg border border-white/15 bg-neutral-900 p-2", className)}>
      <div
        className={cn(
          "relative mx-auto w-full rounded shadow-inner scrollbar-thin",
          // Scrollable viewport so the full-page screenshot + overlay can be inspected.
          fullPage
            ? "max-h-[min(75vh,880px)] overflow-y-scroll overflow-x-hidden overscroll-contain"
            : "max-h-[520px] overflow-y-auto overflow-x-hidden",
          hasImage ? "bg-neutral-950" : "bg-white",
        )}
      >
        {updatingMap && hasImage ? (
          <div className="pointer-events-none sticky top-2 z-30 mx-2 mt-2 inline-flex items-center gap-1.5 rounded-md border border-sky-400/40 bg-black/75 px-2 py-1 text-[10px] font-medium text-sky-100 shadow-lg backdrop-blur-sm">
            <span className="inline-flex h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-200/30 border-t-sky-100" />
            {updatingMapMessage ?? "Updating map…"}
          </div>
        ) : null}

        {/*
          Explicit height from canvas aspect ratio so absolutely-positioned image + overlay
          boxes create a real scrollable document taller than the viewport pane.
        */}
        <div
          className="relative mx-auto w-full"
          style={{ aspectRatio: `${canvasWidth} / ${canvasHeight}` }}
          aria-label="Page screenshot with interaction map overlay"
        >
          {useScrollSlices ? (
            scrollMap!.slices!.map((slice, index) => {
              const src = resolveSliceSrc(capture, slice, undefined, projectPath);
              if (!src) return null;
              const sliceHeight = Math.max(1, slice.height || capture.viewport.height);
              const contentTop = Math.max(0, slice.content_top ?? 0);
              const contentHeight = Math.max(
                0,
                slice.content_height ?? sliceHeight - contentTop,
              );
              if (contentHeight < 1) return null;
              const drawTop = slice.draw_top ?? slice.scroll_y + contentTop;
              return (
                <div
                  key={`${slice.scroll_y}-${index}`}
                  className="absolute left-0 w-full overflow-hidden"
                  style={{
                    top: `${(drawTop / canvasHeight) * 100}%`,
                    height: `${(contentHeight / canvasHeight) * 100}%`,
                  }}
                >
                  <img
                    src={src}
                    alt=""
                    aria-hidden
                    className="pointer-events-none absolute left-0 w-full max-w-none object-fill object-left-top"
                    style={{
                      height: `${(sliceHeight / contentHeight) * 100}%`,
                      top: `${(-contentTop / contentHeight) * 100}%`,
                    }}
                    draggable={false}
                  />
                </div>
              );
            })
          ) : hasImage && screenshotSrc && !liveScreenshot ? (
            <img
              key={screenshotSrc.slice(0, 80)}
              src={screenshotSrc}
              alt=""
              aria-hidden
              className="pointer-events-none absolute inset-0 h-full w-full object-fill object-top"
              draggable={false}
            />
          ) : null}

          {documentCoords && scrollMap?.slices && scrollMap.slice_count > 1
            ? scrollMap.slices.slice(1).map((slice, index) => {
                const drawTop = slice.draw_top ?? slice.scroll_y + (slice.content_top ?? 0);
                if ((slice.content_height ?? 1) < 1) return null;
                return (
                  <div
                    key={`slice-line-${slice.scroll_y}-${index}`}
                    className="pointer-events-none absolute left-0 z-10 w-full border-t border-dashed border-white/20"
                    style={{ top: `${(drawTop / canvasHeight) * 100}%` }}
                    title={
                      slice.delta_from_prev
                        ? `Scrolled ${Math.round(slice.delta_from_prev)}px`
                        : undefined
                    }
                  />
                );
              })
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
              No mapped elements on this page
            </div>
          ) : null}
        </div>
      </div>
      {fullPage || tall ? (
        <p className="mt-2 text-[10px] text-white/45">
          Scroll inside the map to inspect the full page · {Math.round(canvasWidth)} ×{" "}
          {Math.round(canvasHeight)}px
          {scrollMap?.mode === "full_page" ? " · full-page screenshot" : ""}
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
