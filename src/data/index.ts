import { demoMode } from '../config/settings';
import type { DataSource } from './DataSource';
import { LiveSource } from './liveSource';
import { MockSource } from './mockSource';

export const dataSource: DataSource = demoMode ? new MockSource() : new LiveSource();
