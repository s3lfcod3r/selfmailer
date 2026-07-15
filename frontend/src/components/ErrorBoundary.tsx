import { Component, type ErrorInfo, type ReactNode } from "react";

// Wurzel-Fehlergrenze: fängt Render-Fehler einzelner Views ab, damit ein
// kaputtes Rendern nicht die GESAMTE App weißscreent. Zeigt eine freundliche
// Meldung + „Erneut versuchen"/„Neu laden". Keine i18n-Hooks (Klassenkomponente
// kann keine Hooks nutzen) — die Sprache wird einmal aus dem localStorage
// gelesen; DE/EN reichen für den Notfall-Bildschirm.
function isGerman(): boolean {
  try {
    const stored = localStorage.getItem("selfmailer.lang");
    if (stored) return stored === "de";
    return (navigator.language || "").toLowerCase().startsWith("de");
  } catch {
    return false;
  }
}

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Detaillierten Kontext für die Fehlersuche in die Konsole schreiben.
    console.error("[SelfMailer] UI-Fehler abgefangen:", error, info.componentStack);
  }

  private handleRetry = (): void => this.setState({ error: null });
  private handleReload = (): void => window.location.reload();

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    const de = isGerman();
    return (
      <div
        role="alert"
        style={{
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          gap: 14, padding: 32, minHeight: 240, textAlign: "center", color: "var(--self-text)",
        }}
      >
        <div style={{ fontSize: 34 }} aria-hidden>⚠️</div>
        <h2 style={{ margin: 0, fontSize: "1.15rem" }}>
          {de ? "Etwas ist schiefgelaufen" : "Something went wrong"}
        </h2>
        <p className="muted" style={{ margin: 0, maxWidth: 420, lineHeight: 1.5 }}>
          {de
            ? "Diese Ansicht konnte nicht angezeigt werden. Du kannst es erneut versuchen oder die Seite neu laden."
            : "This view could not be displayed. You can retry or reload the page."}
        </p>
        <div style={{ display: "flex", gap: 10, marginTop: 4 }}>
          <button className="ghost" onClick={this.handleRetry}>
            {de ? "Erneut versuchen" : "Retry"}
          </button>
          <button className="primary" onClick={this.handleReload}>
            {de ? "Neu laden" : "Reload"}
          </button>
        </div>
      </div>
    );
  }
}
