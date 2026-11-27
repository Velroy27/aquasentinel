import networkFixture from '../../contracts/mock/network.json';
import scenariosFixture from '../../contracts/mock/scenarios.json';
import resultsFixture from '../../contracts/mock/results.json';
import placementFixture from '../../contracts/mock/placement.json';
import type { Network, Placement, Results, Scenario } from './types';

export const network = networkFixture as Network;
export const scenarios = scenariosFixture as Scenario[];
export const results = resultsFixture as Results;
export const placements = placementFixture as Placement[];