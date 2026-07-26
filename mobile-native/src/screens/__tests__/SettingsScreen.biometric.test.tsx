/**
 * Superseded — see `src/screens/settings/__tests__/SecuritySettingsScreen.test.tsx`.
 *
 * This file covered a Face ID toggle that the settings index used to render
 * inline. The index is now a projection of `src/settings/registry.ts` and owns
 * no security controls at all; the biometric and two-factor behaviour moved to
 * `SecuritySettingsScreen`, where it is covered in more depth than it was here.
 * Index-level coverage lives in `SettingsScreen.test.tsx`.
 *
 * The file remains only because it could not be removed in this environment.
 * It intentionally contains no assertions about biometrics — duplicating them
 * against the wrong screen would be worse than an empty placeholder. Delete it
 * when convenient.
 */

it("has been replaced by SecuritySettingsScreen.test.tsx and SettingsScreen.test.tsx", () => {
  expect(true).toBe(true);
});
