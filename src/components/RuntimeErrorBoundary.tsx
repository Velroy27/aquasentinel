import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode };
type State = { message: string | null };

export class RuntimeErrorBoundary extends Component<Props, State> {
  state: State = { message: null };

  static getDerivedStateFromError(error: Error): State {
    return { message: error.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('AquaSentinel render error', error, info.componentStack);
  }

  render() {
    if (this.state.message) {
      return (
        <main style={{ minHeight: '100vh', padding: 32, background: '#0d1213', color: '#e7ece9', fontFamily: 'IBM Plex Sans, sans-serif' }}>
          <div style={{ maxWidth: 900, margin: '0 auto', borderTop: '2px solid #d97462', paddingTop: 16 }}>
            <div style={{ color: '#d97462', font: '12px IBM Plex Mono, monospace', letterSpacing: '.08em' }}>APP RENDER ERROR</div>
            <h1 style={{ fontSize: 24, fontWeight: 500 }}>AquaSentinel could not render</h1>
            <pre style={{ whiteSpace: 'pre-wrap', color: '#b9c2bf', font: '14px IBM Plex Mono, monospace' }}>{this.state.message}</pre>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
