import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (import.meta.env.DEV) {
      console.error('Unhandled UI error', error, info.componentStack);
    }
  }

  render() {
    if (this.state.error) {
      return (
        <main className="state-page">
          <section className="state-panel" role="alert">
            <h1>Something went wrong</h1>
            <p>The dashboard could not finish loading. Refresh the page and try again.</p>
            <button type="button" onClick={() => window.location.reload()}>
              Refresh
            </button>
          </section>
        </main>
      );
    }
    return this.props.children;
  }
}
