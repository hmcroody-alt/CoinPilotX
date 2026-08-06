/**
 * Authentication uses the same restrained atmosphere as the rest of PulseSoc —
 * which is now the single `PulseBackground` mounted at the app root, so there is
 * nothing left for this component to draw. It previously rendered its own
 * full-screen `GalacticAtmosphere`, whose opaque gradient sat directly on top of
 * the shared layer and hid it on the first screen anyone sees.
 *
 * The component is kept rather than deleted because Login and Signup both place
 * it deliberately in their paint order; removing it would leave the next reader
 * wondering whether the backdrop had been lost.
 */
export function LoginBackground() {
  return null;
}
