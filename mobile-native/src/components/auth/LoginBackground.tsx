import { GalacticAtmosphere } from "../GalacticAtmosphere";

/** Authentication uses the same restrained atmosphere as the rest of PulseSoc. */
export function LoginBackground() {
  return <GalacticAtmosphere variant="feed" testID="authentication-galactic-atmosphere" />;
}
