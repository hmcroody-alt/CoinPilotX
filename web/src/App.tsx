/* Phase 0 placeholder.
 *
 * The scaffold exists to make the build pipeline real -- hashed assets, a
 * freshness gate, CI -- before any product surface depends on it. Phase 1
 * replaces this with the shell, the design system and the marketing surface.
 *
 * It deliberately renders through the token layer rather than with inline
 * styles, so the token wiring is exercised by every build from the first one
 * and a broken import fails visibly here rather than in Phase 1.
 */
export function App() {
  return (
    <main className="app-shell">
      <h1 className="app-shell__title">PulseSoc</h1>
      <p className="app-shell__note">
        Web client scaffold. Built in CI, served as hashed static assets.
      </p>
    </main>
  );
}
