import "react-native-gesture-handler";
import { registerRootComponent } from "expo";
import { LogBox } from "react-native";

if (__DEV__) {
  LogBox.ignoreLogs([
    "[expo-av]: Expo AV has been deprecated",
    "[expo-notifications] Error reading persisted server registration info",
  ]);
}

const App = require("./App").default;

registerRootComponent(App);
