import test from "node:test";
import assert from "node:assert/strict";
import {
  clampView,
  concatSamples,
  splitAtBounds,
  encodeWav,
  joinRegions,
  MIN_VIEW_S,
  normalizeRegions,
  panView,
  peaks,
  resultToSourceTime,
  totalDuration,
  zoomView,
} from "./audioEdit.js";

test("normalizeRegions clamps, drops slivers, sorts and merges", () => {
  const out = normalizeRegions(
    [
      { start: 5, end: 3 },
      { start: 1, end: 1.01 },
      { start: 2.5, end: 4 },
      { start: -1, end: 0.5 },
      { start: 9, end: 20 },
    ],
    10,
  );
  assert.deepEqual(out, [
    { start: 0, end: 0.5 },
    { start: 2.5, end: 5 },
    { start: 9, end: 10 },
  ]);
});

test("concatSamples lays clips end to end with a gap and reports bounds", () => {
  const rate = 100;
  const { samples, bounds } = concatSamples([new Float32Array(200).fill(1), new Float32Array(100).fill(2)], rate, 0.5);
  assert.equal(samples.length, 200 + 50 + 100);
  assert.deepEqual(bounds, [{ start: 0, end: 2 }, { start: 2.5, end: 3.5 }]);
  assert.equal(samples[199], 1);
  assert.equal(samples[200], 0);
  assert.equal(samples[250], 2);
});

test("concatSamples with one clip is that clip", () => {
  const { samples, bounds } = concatSamples([new Float32Array(300).fill(1)], 100);
  assert.equal(samples.length, 300);
  assert.deepEqual(bounds, [{ start: 0, end: 3 }]);
});

test("splitAtBounds cuts regions at clip edges and drops the gap", () => {
  const bounds = [{ start: 0, end: 2 }, { start: 2.5, end: 4 }];
  assert.deepEqual(splitAtBounds([{ start: 1, end: 3 }], bounds), [
    { start: 1, end: 2 },
    { start: 2.5, end: 3 },
  ]);
  assert.deepEqual(splitAtBounds([{ start: 2.1, end: 2.4 }], bounds), []);
  assert.deepEqual(splitAtBounds([{ start: 0.5, end: 1.5 }], bounds), [{ start: 0.5, end: 1.5 }]);
});

test("totalDuration sums regions", () => {
  assert.equal(totalDuration([{ start: 0, end: 1 }, { start: 2, end: 4.5 }]), 3.5);
});

test("joinRegions concatenates in order and skips the gap", () => {
  const rate = 100;
  const src = new Float32Array(500).fill(1);
  src.fill(0.5, 100, 200); // noise zone 1s-2s
  const out = joinRegions(src, rate, [{ start: 0, end: 1 }, { start: 2, end: 3 }], 0);
  assert.equal(out.length, 200);
  assert.ok(out.every((s) => s === 1));
});

test("joinRegions fades the seams", () => {
  const rate = 1000;
  const src = new Float32Array(3000).fill(1);
  const out = joinRegions(src, rate, [{ start: 0, end: 1 }, { start: 2, end: 3 }], 0.01);
  assert.equal(out[0], 0);
  assert.equal(out[999], 0);
  assert.equal(out[1000], 0);
  assert.equal(out[500], 1);
});

test("resultToSourceTime maps across a cut", () => {
  const regions = [{ start: 0, end: 2 }, { start: 5, end: 8 }];
  assert.equal(resultToSourceTime(regions, 1), 1);
  assert.equal(resultToSourceTime(regions, 3), 6);
  assert.equal(resultToSourceTime(regions, 99), 8);
});

test("encodeWav writes a valid mono 16-bit header", () => {
  const buf = encodeWav(new Float32Array([0, 1, -1]), 24000);
  const v = new DataView(buf);
  const tag = (o) => String.fromCharCode(...[0, 1, 2, 3].map((i) => v.getUint8(o + i)));
  assert.equal(tag(0), "RIFF");
  assert.equal(tag(8), "WAVE");
  assert.equal(v.getUint16(22, true), 1);
  assert.equal(v.getUint32(24, true), 24000);
  assert.equal(v.getUint32(40, true), 6);
  assert.equal(v.getInt16(46, true), 32767);
  assert.equal(v.getInt16(48, true), -32768);
  assert.equal(buf.byteLength, 50);
});

test("peaks returns min/max per bucket", () => {
  const p = peaks(new Float32Array([0.2, -0.4, 0.9, -0.1]), 2);
  assert.deepEqual([...p].map((x) => +x.toFixed(2)), [-0.4, 0.2, -0.1, 0.9]);
});

test("zoomView keeps the time under the cursor fixed", () => {
  const full = { start: 0, span: 10 };
  const z = zoomView(full, 10, 0.25, 2);
  assert.deepEqual(z, { start: 1.25, span: 5 });
  assert.equal(z.start + 0.25 * z.span, 2.5); // 2.5 s was under 25% of the full view
  const again = zoomView(z, 10, 0.5, 2);
  assert.equal(again.start + 0.5 * again.span, z.start + 0.5 * z.span);
});

test("zoomView stays inside the clip and above the minimum span", () => {
  assert.deepEqual(zoomView({ start: 0, span: 10 }, 10, 0.5, 0.5), { start: 0, span: 10 }); // cannot zoom out past full
  const deep = zoomView({ start: 4, span: 0.3 }, 10, 0.5, 100);
  assert.equal(deep.span, MIN_VIEW_S);
  const edge = zoomView({ start: 0, span: 10 }, 10, 1, 4);
  assert.deepEqual(edge, { start: 7.5, span: 2.5 });
});

test("panView and clampView clamp to the clip", () => {
  assert.deepEqual(panView({ start: 2, span: 4 }, 10, 100), { start: 6, span: 4 });
  assert.deepEqual(panView({ start: 2, span: 4 }, 10, -100), { start: 0, span: 4 });
  assert.deepEqual(clampView({ start: -3, span: 99 }, 10), { start: 0, span: 10 });
  assert.deepEqual(clampView({ start: 0, span: 1 }, 0.1), { start: 0, span: 0.1 }); // clip shorter than the minimum
});
