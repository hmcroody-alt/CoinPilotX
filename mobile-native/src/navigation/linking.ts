import { LinkingOptions } from "@react-navigation/native";
import { RootStackParamList } from "./types";

export const linking: LinkingOptions<RootStackParamList> = {
  prefixes: ["pulsesoc://", "https://pulsesoc.com"],
  config: {
    screens: {
      Tabs: {
        screens: {
          Messenger: "pulse/messages"
        }
      },
      Chat: {
        path: "pulse/messages/:conversationId",
        parse: {
          conversationId: Number
        }
      }
    }
  }
};
