import React from "react";
import { useAuthStore } from "../store/authStore";
import { AuthNavigator } from "./AuthNavigator";
import { MainNavigator } from "./MainNavigator";

export function AppNavigator() {
  const signedIn = useAuthStore(state => state.signedIn);
  return signedIn ? <MainNavigator /> : <AuthNavigator />;
}
