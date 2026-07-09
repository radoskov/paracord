import { describe, expect, it } from 'vitest';

import type { QueueStatus } from '../api/client';
import { deriveJobsBadge } from './jobsHealth';

function status(overrides: Partial<QueueStatus>): QueueStatus {
  return {
    available: true,
    workers: 1,
    counts: { queued: 0, started: 0, finished: 0, failed: 0, scheduled: 0, deferred: 0 },
    jobs: [],
    ...overrides,
  };
}

describe('deriveJobsBadge (nav Jobs indicator, issue 4)', () => {
  it('is grey when status is unknown', () => {
    expect(deriveJobsBadge(null).color).toBe('grey');
  });

  it('is red when Redis is unreachable', () => {
    const b = deriveJobsBadge(status({ redis_reachable: false, available: false, worker_count: 0 }));
    expect(b.color).toBe('red');
  });

  it('is yellow when reachable but no workers are running', () => {
    const b = deriveJobsBadge(status({ redis_reachable: true, worker_count: 0, queued: 4 }));
    expect(b.color).toBe('yellow');
    expect(b.queued).toBe(4);
  });

  it('is green when healthy and idle', () => {
    const b = deriveJobsBadge(
      status({ redis_reachable: true, worker_count: 2, queued: 0, counts: { queued: 0, started: 0, finished: 9, failed: 0, scheduled: 0, deferred: 0 } }),
    );
    expect(b.color).toBe('green');
    expect(b.queued).toBe(0);
  });

  it('is blue when jobs are running', () => {
    const b = deriveJobsBadge(
      status({ redis_reachable: true, worker_count: 2, queued: 0, counts: { queued: 0, started: 3, finished: 0, failed: 0, scheduled: 0, deferred: 0 } }),
    );
    expect(b.color).toBe('blue');
    expect(b.running).toBe(3);
  });

  it('is blue with a queued count when work is waiting on a healthy queue', () => {
    const b = deriveJobsBadge(
      status({ redis_reachable: true, worker_count: 2, queued: 7, counts: { queued: 7, started: 0, finished: 0, failed: 0, scheduled: 0, deferred: 0 } }),
    );
    expect(b.color).toBe('blue');
    expect(b.queued).toBe(7);
  });
});
