import type { QueueStatus } from '../api/client';

/**
 * Compact job-queue indicator shown next to the "Jobs" nav tab (issue 4).
 *
 * Mirrors the JobsPage queue-health semaphore's green/yellow/red semantics so the two never
 * disagree, and adds a blue "work in progress" state when the queue is healthy but has active or
 * queued jobs. `queued` drives the small `[N]` count badge.
 *
 * - grey  — status not yet known (first poll pending / poll failed)
 * - red   — Redis unreachable (imports won't be processed)
 * - yellow— reachable but no worker consuming (jobs pile up)
 * - blue  — reachable + workers present + something running or queued
 * - green — reachable + workers present + idle
 */
export type JobsBadgeColor = 'grey' | 'red' | 'yellow' | 'blue' | 'green';

export interface JobsBadge {
  color: JobsBadgeColor;
  queued: number;
  running: number;
  title: string;
}

/** Map raw queue status into the badge color/counts/tooltip described in {@link JobsBadgeColor}. */
export function deriveJobsBadge(status: QueueStatus | null): JobsBadge {
  if (!status) {
    return { color: 'grey', queued: 0, running: 0, title: 'Job queue status unknown' };
  }
  const reachable = status.redis_reachable ?? status.available;
  const workerCount = status.worker_count ?? status.workers ?? 0;
  const queued = status.queued ?? status.counts?.queued ?? 0;
  const running = status.counts?.started ?? 0;

  if (!reachable) {
    return {
      color: 'red',
      queued,
      running,
      title: status.require_redis
        ? 'Rate & queue limits unavailable — Redis unreachable and required'
        : "Processing queue offline (Redis unreachable) — imports won't be processed",
    };
  }
  if (workerCount === 0) {
    return { color: 'yellow', queued, running, title: `No workers running · ${queued} queued` };
  }
  if (running > 0 || queued > 0) {
    return {
      color: 'blue',
      queued,
      running,
      title: `${running} running · ${queued} queued`,
    };
  }
  return {
    color: 'green',
    queued,
    running,
    title: `Queue healthy · ${workerCount} worker${workerCount === 1 ? '' : 's'}`,
  };
}
