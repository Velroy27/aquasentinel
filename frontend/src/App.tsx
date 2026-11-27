import { useEffect } from 'react';
import { HashRouter, Route, Routes } from 'react-router-dom';
import { OperatorShell } from './components/OperatorShell';
import { CostPage } from './pages/CostPage';
import { HomePage } from './pages/HomePage';
import { PlacementPage } from './pages/PlacementPage';
import { ResultsPage } from './pages/ResultsPage';
import { useDashboard } from './store/useDashboard';
import { RuntimeErrorBoundary } from './components/RuntimeErrorBoundary';

export default function App() {
  const init = useDashboard((s) => s.init);
  useEffect(() => { void init(); }, [init]);

  return (
    <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route element={<OperatorShell />}>
          <Route index element={<RuntimeErrorBoundary><HomePage /></RuntimeErrorBoundary>} />
          <Route path="placement" element={<RuntimeErrorBoundary><PlacementPage /></RuntimeErrorBoundary>} />
          <Route path="results" element={<RuntimeErrorBoundary><ResultsPage /></RuntimeErrorBoundary>} />
          <Route path="cost" element={<RuntimeErrorBoundary><CostPage /></RuntimeErrorBoundary>} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
