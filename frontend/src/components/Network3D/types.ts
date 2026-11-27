import type { Junction } from '../../data/types';

export type CameraPreset = 'top' | 'angled' | 'follow' | 'custom';

export type Network3DProps = {
  selectedSensor: string | null;
  onSelectSensor?: (id: string) => void;
  highlightSensors?: string[];
  showLocalization?: boolean;
  heatmapEnabled?: boolean;
  onHeatmapChange?: (enabled: boolean) => void;
  revealLeak?: boolean;
  onRevealLeakChange?: (enabled: boolean) => void;
  title?: string;
  subtitle?: string;
  emptyNotice?: string;
  onToggleViewMode?: () => void;
};

export type Node3DData = {
  junction: Junction;
  x: number;
  y: number; // elevation height
  z: number;
  isSensor: boolean;
  rank?: number;
};
