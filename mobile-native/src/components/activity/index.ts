/**
 * Barrel for the shared Activity components. The Activity feed screen imports the
 * header, notification rows and domain type-circle from here, so every seller
 * surface that opens the feed renders the same derivation through the same parts.
 */

export { TypeCircle } from "./TypeCircle";
export { NotificationRow } from "./NotificationRow";
export { ActivityHeader } from "./ActivityHeader";
