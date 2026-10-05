import { test } from "node:test";
import assert from "node:assert/strict";
import { formatPercent } from "../src/format";
import { summarize } from "../src/report";

test("formatPercent rounds half up", () => {
  assert.equal(formatPercent(0.666), "67%");
});

test("formatPercent rounds at half boundary", () => {
  assert.equal(formatPercent(0.005), "1%");
});

test("formatPercent handles one", () => {
  assert.equal(formatPercent(1.0), "100%");
});

test("summarize reports No data for empty input", () => {
  assert.equal(summarize([]), "No data");
});

test("summarize sums multiple values", () => {
  assert.equal(summarize([0.1, 0.2, 0.3]), "60%");
});

test("summarize rounds a near-total", () => {
  assert.equal(summarize([0.333, 0.333, 0.333]), "100%");
});
