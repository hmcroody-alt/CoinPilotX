import { PulseBackground } from "./components/PulseBackground";

/* Phase 1 shell.
 *
 * The scaffold exists to make the build pipeline real -- hashed assets, a
 * freshness gate, CI -- before any product surface depends on it. Phase 1
 * layers the design system onto it a piece at a time; the marketing surface
 * and the feed itself come next.
 *
 * It deliberately renders through the token layer rather than with inline
 * styles, so the token wiring is exercised by every build from the first one
 * and a broken import fails visibly here rather than later.
 *
 * The backdrop is `fixed` because it is the page's field and not a section's.
 * An absolute one would scroll away partway down and leave the palette's flat
 * background behind it, which is the one thing the app never shows.
 *
 * The rails are rendered empty on purpose. `layout.css` decides at which
 * widths each column exists, and the only way to know that decision survives a
 * refactor is for the grid to have all three tracks in the built output from
 * the first commit -- an empty rail that appears at 900px is a testable claim,
 * whereas a rail that arrives with its content in a later phase is an
 * untested media query nobody has ever seen fire.
 *
 * Nothing here links to the App Store yet, and that is deliberate rather than
 * an omission: the canonical link lives in `services/app_links.py` and must not
 * be hand-written a second time in the client. Phase 1d wires the CTA to that
 * source, which is also when the rails get their real content.
 */
export function App() {
  return (
    <>
      <PulseBackground fixed />
      <div className="pulse-shell">
        {/* `aria-hidden` while empty: an empty landmark is worse than no
          * landmark, because a screen reader announces a region and then finds
          * nothing inside it. Phase 1d replaces this with a real nav. */}
        <aside className="pulse-shell__rail pulse-shell__rail--command" aria-hidden="true" />

        <main className="pulse-shell__feed app-shell">
          <h1 className="app-shell__title">PulseSoc</h1>
          <p className="app-shell__note">
            Web client scaffold. Built in CI, served as hashed static assets.
          </p>
        </main>

        <aside className="pulse-shell__rail pulse-shell__rail--side" aria-hidden="true" />
      </div>
    </>
  );
}
