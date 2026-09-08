/** Per-account/folder cooldown. Cache reads never count as a successful sync. */
export class SyncBackoff {
  private entries = new Map<string, { failures: number; next: number }>();
  constructor(private readonly now = Date.now) {}

  due(key: string): boolean { return this.now() >= (this.entries.get(key)?.next ?? 0); }

  record(key: string, outcome: "success" | "busy" | "error"): void {
    if (outcome === "success") { this.entries.delete(key); return; }
    const failures = Math.min((this.entries.get(key)?.failures ?? 0) + 1, 8);
    const delay = Math.min(20_000 * 2 ** (failures - 1), outcome === "busy" ? 120_000 : 300_000);
    this.entries.delete(key);
    this.entries.set(key, { failures, next: this.now() + delay });
    while (this.entries.size > 128) this.entries.delete(this.entries.keys().next().value!);
  }
}
