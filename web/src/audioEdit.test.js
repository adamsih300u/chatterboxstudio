import test from "node:test";
import assert from "node:assert/strict";
import {
  encodeWav,
  joinRegions,
  normalizeRegions,
  peaks,
  resultToSourceTime,
  totalDuration,
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
