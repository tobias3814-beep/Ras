import { Component, type ReactNode } from "react";

/** Keeps one broken section from blanking the whole app: shows what failed and lets you retry or switch tabs. */
export class SectionGuard extends Component<{ name: string; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(`[${this.props.name}]`, error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="rounded-lg border border-hud-red/50 bg-hud-panel/60 p-4 text-sm">
        <p className="font-bold text-hud-red">This section hit an error, so it isn't shown. The rest of Ras keeps running.</p>
        <p className="mt-1 font-mono text-xs text-slate-400">{this.state.error.message}</p>
        <button className="mt-3 rounded border border-hud-accent px-3 py-0.5 text-xs text-hud-accent" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}
