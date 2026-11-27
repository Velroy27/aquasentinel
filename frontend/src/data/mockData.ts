import networkFixture from '../../../contracts/mock/network.json';
import scenariosFixture from '../../../contracts/mock/scenarios.json';
import resultsFixture from '../../../data/results.json';
import type { Network, Placement, Results, Scenario } from './types';
import { normalizeResults } from './normalizeResults';
import { normalizeNetwork, normalizeScenarios } from './normalizeNetwork';

export const network: Network = normalizeNetwork(networkFixture);
export const scenarios: Scenario[] = normalizeScenarios(scenariosFixture);
export const results: Results = normalizeResults(resultsFixture);
export const placements: Placement[] = [];
