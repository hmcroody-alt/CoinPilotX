import React from "react";
import { useAuthStore } from "../store/authStore";
import { AuthNavigator } from "./AuthNavigator";
import { MainTabs } from "./MainTabs";

export function AppNavigator() {
  const signedIn = useAuthStore(state => state.signedIn);
  return signedIn ? <MainTabs /> : <AuthNavigator />;
}
