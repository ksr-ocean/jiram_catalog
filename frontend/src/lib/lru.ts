/** A tiny insertion-ordered LRU; the frame cache is the only user. */
export class Lru<K, V> {
  private readonly map = new Map<K, V>();

  constructor(private readonly capacity: number, private readonly dispose?: (value: V) => void) {}

  get(key: K): V | undefined {
    const value = this.map.get(key);
    if (value === undefined) return undefined;
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  has(key: K): boolean {
    return this.map.has(key);
  }

  set(key: K, value: V): void {
    if (this.map.has(key)) this.map.delete(key);
    this.map.set(key, value);
    while (this.map.size > this.capacity) {
      const oldest = this.map.keys().next();
      if (oldest.done) break;
      const evicted = this.map.get(oldest.value);
      this.map.delete(oldest.value);
      if (evicted !== undefined) this.dispose?.(evicted);
    }
  }

  get size(): number {
    return this.map.size;
  }

  clear(): void {
    if (this.dispose) for (const value of this.map.values()) this.dispose(value);
    this.map.clear();
  }
}
