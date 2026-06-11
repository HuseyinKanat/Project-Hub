const UINT32_MAX_EXCLUSIVE = 0x100000000;

function fallbackInt(maxExclusive: number): number {
  const now = Date.now() + (typeof performance !== "undefined" ? performance.now() : 0);
  return Math.floor(now % maxExclusive);
}

export function secureRandomInt(maxExclusive: number): number {
  if (!Number.isInteger(maxExclusive) || maxExclusive <= 0) {
    throw new Error("maxExclusive must be a positive integer");
  }

  const cryptoApi = globalThis.crypto;
  if (!cryptoApi?.getRandomValues) {
    return fallbackInt(maxExclusive);
  }

  const limit = Math.floor(UINT32_MAX_EXCLUSIVE / maxExclusive) * maxExclusive;
  const buffer = new Uint32Array(1);
  let value = 0;

  do {
    cryptoApi.getRandomValues(buffer);
    value = buffer[0] ?? 0;
  } while (value >= limit);

  return value % maxExclusive;
}
