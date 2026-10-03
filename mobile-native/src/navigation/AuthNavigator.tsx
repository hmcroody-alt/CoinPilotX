import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { LoginScreen } from "../screens/LoginScreen";
import { SignupScreen } from "../screens/SignupScreen";
import { AccountRecoveryScreen } from "../screens/AccountRecoveryScreen";
import { FederatedSignupScreen } from "../screens/FederatedSignupScreen";
import { AuthStackParamList } from "./types";

const Stack = createNativeStackNavigator<AuthStackParamList>();

export function AuthNavigator() {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="Login" component={LoginScreen} />
      <Stack.Screen name="Signup" component={SignupScreen} />
      <Stack.Screen name="AccountRecovery" component={AccountRecoveryScreen} />
      {/* `gestureEnabled: false` because a swipe-back here would strand a
          verified provider identity with no consent and no account, and the
          ticket would expire unspent. Leaving is the explicit Cancel action,
          which returns to Login where the member can start over. */}
      <Stack.Screen name="FederatedSignup" component={FederatedSignupScreen} options={{ gestureEnabled: false }} />
    </Stack.Navigator>
  );
}
