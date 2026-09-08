/** Bounded, per-component, memory-only cache; pending requests share a promise. */
export class RequestCache<T> {
  private entries = new Map<string, { promise: Promise<T>; expires: number }>();
  constructor(private readonly maxEntries = 32, private readonly ttlMs = 15_000, private readonly now = Date.now) {}

  get(key: string, fetcher: () => Promise<T>): Promise<T> {
    const time = this.now();
    for (const [k, entry] of this.entries) if (entry.expires <= time) this.entries.delete(k);
    const cached = this.entries.get(key);
    if (cached) {
      this.entries.delete(key); this.entries.set(key, cached);
      return cached.promise;
    }
    while (this.entries.size >= Math.max(1, this.maxEntries)) this.entries.delete(this.entries.keys().next().value!);
    // Keep deduplicating slow in-flight requests; the entry count is bounded even
    // if a request never settles. Only completed results get a short lifetime.
    const entry = { promise: Promise.resolve().then(fetcher), expires: Infinity };
    this.entries.set(key, entry);
    entry.promise.then(() => {
      if (this.entries.get(key) === entry) entry.expires = this.now() + this.ttlMs;
    }, () => {
      if (this.entries.get(key) === entry) this.entries.delete(key);
    });
    return entry.promise;
  }

  // Old pending responses cannot repopulate the cache after invalidation.
  clear(): void { this.entries.clear(); }
}
