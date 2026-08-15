export type CommunityCreateIntent = "group" | "room";

let pendingIntent: CommunityCreateIntent | null = null;

export function setCommunityCreateIntent(intent: CommunityCreateIntent) {
  pendingIntent = intent;
}

export function takeCommunityCreateIntent() {
  const intent = pendingIntent;
  pendingIntent = null;
  return intent;
}
