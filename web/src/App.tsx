import { PulseBackground } from "./components/PulseBackground";

/* Phase 1 shell.
 *
 * The scaffold exists to make the build pipeline real -- hashed assets, a
 * freshness gate, CI -- before any product surface depends on it. Phase 1
 * layers the design system onto it a piece at a time; the responsive grid and
 * the marketing surface come next.
 *
 * It deliberately renders through the token layer rather than with inline
 * styles, so the token wiring is exercised by every build from the first one
 * and a broken import fails visibly here rather than later.
 *
 * The backdrop is `fixed` because it is the page's field and not a section's.
 * An absolute one would scroll away partway down and leave the palette's flat
 * background behind it, which is the one thing the app never shows.
 */
export function App() {
  return (
    <>
      <PulseBackground fixed />
      <main className="app-shell">
        <h1 className="app-shell__title">PulseSoc</h1>
        <p className="app-shell__note">
          Web client scaffold. Built in CI, served as hashed static assets.
        </p>
      </main>
    </>
  );
}
