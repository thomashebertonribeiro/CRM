import logger from './logger.js';

class CacheManager {
  constructor(ttlMs = 24 * 60 * 60 * 1000) {
    this.cache = new Map();
    this.ttl = ttlMs;
    this.cleanupInterval = setInterval(() => this.cleanupExpired(), 60 * 60 * 1000);
    this.cleanupInterval.unref();
  }

  get(key) {
    const cached = this.cache.get(key);
    if (!cached) return null;
    if (Date.now() - cached.timestamp >= this.ttl) {
      this.cache.delete(key);
      return null;
    }
    logger.debug(`Cache hit: ${key}`);
    return cached.data;
  }

  set(key, data) {
    this.cache.set(key, { data, timestamp: Date.now() });
    logger.debug(`Cached: ${key}`);
  }

  delete(key) {
    return this.cache.delete(key);
  }

  clear() {
    this.cache.clear();
  }

  getStats() {
    return { size: this.cache.size, ttlMs: this.ttl };
  }

  cleanupExpired() {
    let removed = 0;
    const now = Date.now();
    for (const [key, cached] of this.cache.entries()) {
      if (now - cached.timestamp >= this.ttl) {
        this.cache.delete(key);
        removed++;
      }
    }
    if (removed > 0) logger.info(`Cache cleanup: removed ${removed} expired entries`);
  }
}

const cacheManager = new CacheManager(24 * 60 * 60 * 1000);
export default cacheManager;
