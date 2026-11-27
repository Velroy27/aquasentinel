import React from 'react';
import ReactDOM from 'react-dom/client';
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-sans/700.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import App from './App';
import { RuntimeErrorBoundary } from './components/RuntimeErrorBoundary';
import './styles/tokens.css';
import './styles/layout.css';
import './styles/map.css';
import './styles/panels.css';
import './styles/pages.css';
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RuntimeErrorBoundary>
      <App />
    </RuntimeErrorBoundary>
  </React.StrictMode>,
);
