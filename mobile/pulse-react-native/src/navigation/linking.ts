import { PULSE_DEEP_LINK_PREFIXES } from "../config";

export const linking = {
  prefixes: PULSE_DEEP_LINK_PREFIXES,
  config: {
    screens: {
      Feed: "pulse",
      Reels: "pulse/reels",
      Videos: "pulse/videos",
      Messages: "pulse/messages-v2",
      Notifications: "pulse/notifications",
      Profile: "pulse/profile",
    },
  },
};
