import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = {
  fallbackNotice?: string;
  onFallback: () => void;
  children: ReactNode;
};

type State = {
  hasError: boolean;
  errorMessage: string | null;
};

export class ErrorBoundary3D extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, errorMessage: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      errorMessage: error.message || 'WebGL / 3D Graphics initialization failed',
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('3D View encountered a rendering error:', error, info);
    // Trigger immediate fallback to 2D map
    this.props.onFallback();
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="panel map-shell error-state" role="alert" style={{ textAlign: 'center', padding: '24px' }}>
          <div style={{ color: 'var(--amber)', fontSize: '15px', fontWeight: 600, marginBottom: '8px' }}>
            WebGL / 3D Initialization Failed
          </div>
          <p style={{ color: 'var(--muted)', fontSize: '13px', margin: '0 0 16px' }}>
            {this.props.fallbackNotice || 'Hardware acceleration or WebGL context is unavailable. Reverting to 2D live map.'}
          </p>
          <button type="button" className="map-toggle" onClick={() => this.props.onFallback()}>
            Return to 2D Map
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
