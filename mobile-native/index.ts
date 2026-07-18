import "react-native-gesture-handler";
import { registerRootComponent } from "expo";
import { LogBox } from "react-native";

if (__DEV__) {
  LogBox.ignoreLogs(["[expo-av]: Expo AV has been deprecated"]);
}

const App = require("./App").default;

registerRootComponent(App);
