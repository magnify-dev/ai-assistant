import assert from "node:assert/strict";
import test from "node:test";
import {
  boxTone,
  captureBoxStyle,
  filterCaptureElements,
  isCaptureMapReady,
} from "../src/lib/webCaptureView.ts";

const capture = {
  viewport: { width: 1000, height: 500 },
};

const kept = {
  id: "keep",
  kind: "button",
  rect: { x: 100, y: 50, width: 200, height: 100 },
  locator_status: "unique",
  ai_interactive: true,
  user_interactive: null,
  map_matched: false,
};
const saved = {
  id: "saved",
  kind: "button",
  rect: { x: 0, y: 0, width: 10, height: 10 },
  locator_status: "unique",
  ai_interactive: false,
  user_interactive: true,
  map_matched: true,
};
const problem = {
  id: "problem",
  kind: "button",
  rect: { x: 0, y: 0, width: 10, height: 10 },
  locator_status: "ambiguous",
  ai_interactive: false,
  user_interactive: null,
  map_matched: false,
};

test("scales captured geometry into viewport percentages", () => {
  assert.deepEqual(captureBoxStyle(kept, capture), {
    left: "10%",
    top: "10%",
    width: "20%",
    height: "20%",
  });
});

test("stitched document canvas uses scroll_map height for box math", async () => {
  const { captureBoxStyle: boxStyle, captureCanvasHeight } = await import(
    "../src/lib/webCaptureView.ts"
  );
  const stitched = {
    viewport: { width: 1000, height: 500 },
    scroll_map: {
      stitched: true,
      coords: "document",
      canvas_height: 2000,
      slice_count: 3,
    },
  };
  assert.equal(captureCanvasHeight(stitched), 2000);
  assert.deepEqual(
    boxStyle(
      {
        id: "deep",
        kind: "link",
        rect: { x: 100, y: 1000, width: 200, height: 100 },
        locator_status: "unique",
      },
      stitched,
    ),
    {
      left: "10%",
      top: "50%",
      width: "20%",
      height: "5%",
    },
  );
});

test("filters effective, saved, and locator problems", () => {
  assert.deepEqual(filterCaptureElements([kept, saved, problem], "kept"), [kept, saved]);
  assert.deepEqual(filterCaptureElements([kept, saved, problem], "saved"), [saved]);
  assert.deepEqual(filterCaptureElements([kept, saved, problem], "problems"), [problem]);
});

test("isCaptureMapReady requires stitched full-page image", () => {
  assert.equal(
    isCaptureMapReady({
      viewport: { width: 1280, height: 720 },
      elements: [],
      scroll_map: { stitched: false, canvas_height: 720, slice_count: 1, slices: [] },
    }),
    false,
  );
  assert.equal(
    isCaptureMapReady({
      viewport: { width: 1280, height: 720 },
      screenshot: "screenshots/page.jpg",
      scroll_map: {
        stitched: true,
        mode: "full_page",
        coords: "document",
        canvas_height: 4000,
        slice_count: 1,
        slices: [{ scroll_y: 0, height: 4000, screenshot: "screenshots/page.jpg" }],
      },
    }),
    true,
  );
});

test("prioritizes saved map tone over AI tone", () => {
  assert.match(boxTone(saved), /violet/);
});

test("parses visual tile cells", async () => {
  const { parseVisualCell, visualCellStyle } = await import("../src/lib/webCaptureVisual.ts");
  const cell = parseVisualCell("#336699|button");
  assert.equal(cell.kind, "button");
  assert.equal(visualCellStyle(cell).backgroundColor, "#336699");
});
