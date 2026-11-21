/**
 * Theater-Safe React Error Boundary for StageState Frontend.
 *
 * Feature & Use Cases:
 * - Prevents white-screen crashes during live hackathon demos or video capture
 *   if an unexpected runtime or WebGL error occurs.
 * - Offers instant 1-click "Reset Stage" (remounts tree) and "Reload App"
 *   recovery actions in matching dark-theater styling.
 */
import React from 'react';

interface StageErrorBoundaryProps {
  children: React.ReactNode;
}

interface StageErrorBoundaryState {
  hasError: boolean;
  errorMessage: string;
}

export class StageErrorBoundary extends React.Component<
  StageErrorBoundaryProps,
  StageErrorBoundaryState
> {
  constructor(props: StageErrorBoundaryProps) {
    super(props);
    this.state = {
      hasError: false,
      errorMessage: '',
    };
  }

  static getDerivedStateFromError(error: Error): StageErrorBoundaryState {
    return {
      hasError: true,
      errorMessage: error?.message || 'Unexpected stage rendering error.',
    };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error('[StageState] Caught UI error:', error, info.componentStack);
  }

  private handleReset = (): void => {
    this.setState({ hasError: false, errorMessage: '' });
  };

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): React.ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 24,
          background: '#090b12',
          color: '#f1f5f9',
        }}
      >
        <div
          style={{
            maxWidth: 480,
            width: '100%',
            background: '#101420',
            border: '1px solid rgba(251, 191, 36, 0.35)',
            borderRadius: 14,
            padding: 20,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            boxShadow: '0 20px 50px rgba(0, 0, 0, 0.5)',
          }}
        >
          <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#fde68a' }}>
            StageState · Instant Stage Recovery
          </div>
          <div style={{ fontSize: '0.86rem', color: '#94a3b8', lineHeight: 1.5 }}>
            A runtime rendering hiccup was intercepted before interrupting the
            stage. You can recover cleanly with one click.
          </div>
          <code
            style={{
              fontSize: '0.75rem',
              padding: '8px 10px',
              borderRadius: 8,
              background: '#090b12',
              color: '#fda4af',
              wordBreak: 'break-word',
            }}
          >
            {this.state.errorMessage}
          </code>
          <div style={{ display: 'flex', gap: 10 }}>
            <button
              type="button"
              className="pill-btn active"
              onClick={this.handleReset}
            >
              ↺ Recover Stage
            </button>
            <button
              type="button"
              className="pill-btn blue"
              onClick={this.handleReload}
            >
              🔄 Reload App
            </button>
          </div>
        </div>
      </div>
    );
  }
}
