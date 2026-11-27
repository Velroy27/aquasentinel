import { useEffect } from 'react';
import { HashRouter, Route, Routes } from 'react-router-dom';
import { OperatorShell } from './components/OperatorShell';
import { CostPage } from './pages/CostPage';
import { HomePage } from './pages/HomePage';
import { PlacementPage } from './pages/PlacementPage';
import { ResultsPage } from './pages/ResultsPage';
import { useDashboard } from './store/useDashboard';

export default function App() {
  const init = useDashboard((s) => s.init);
  useEffect(() => { void init(); }, [init]);

  return (
    <HashRouter>
      <Routes>
        <Route element={<OperatorShell />}>
          <Route index element={<HomePage />} />
          <Route path="placement" element={<PlacementPage />} />
          <Route path="results" element={<ResultsPage />} />
          <Route path="cost" element={<CostPage />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
