// Types for audioEdit.js, which stays plain JS so `node --test` can run it without a build step.
export interface Region {
  start: number;
  end: number;
}

export const MIN_REGION_S: number;
export const FADE_S: number;
export function normalizeRegions(regions: Region[], duration: number): Region[];
export function totalDuration(regions: Region[]): number;
export function joinRegions(samples: Float32Array<ArrayBuffer>, rate: number, regions: Region[], fadeS?: number): Float32Array<ArrayBuffer>;
export function resultToSourceTime(regions: Region[], t: number): number;
export function encodeWav(samples: Float32Array<ArrayBuffer>, rate: number): ArrayBuffer;
export function peaks(samples: Float32Array<ArrayBuffer>, buckets: number): Float32Array<ArrayBuffer>;
export function lengthWarning(seconds: number): string;
