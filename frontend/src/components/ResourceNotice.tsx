import type { ReactNode } from 'react';
import type { ResourceKey } from '../data/types';
import { useDashboard } from '../store/useDashboard';

export function ResourceNotice({ resource, children }: { resource: ResourceKey; children: ReactNode }) {
  const status = useDashboard((state) => state.resources[resource]);
  const retry = useDashboard((state) => state.retryResource);
  if (status.loading) return <div className="panel resource-state" role="status">Loading {resource}…</div>;
  if (status.error) return <div className="panel resource-state error-state" role="alert"><span>{resource.toUpperCase()} · {status.error}</span><button type="button" onClick={() => void retry(resource)}>Retry</button></div>;
  return <>{children}</>;
}
