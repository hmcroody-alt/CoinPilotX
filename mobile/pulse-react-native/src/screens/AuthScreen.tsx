import React, { useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, Text, TextInput, TouchableOpacity, View } from "react-native";
import { useAuth } from "../auth/AuthProvider";
import { colors, screenStyles } from "../styles/theme";

type AuthMode = "login" | "register" | "recover";

export function AuthScreen() {
  const auth = useAuth();
  const [mode, setMode] = useState<AuthMode>("login");
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function submit() {
    setBusy(true);
    setMessage("");
    try {
      if (mode === "register") {
        await auth.register({ full_name: fullName, username, email, password });
        setMessage("Check your email to confirm your Pulse account.");
      } else if (mode === "recover") {
        await auth.recover(email);
        setMessage("If an account exists, password recovery has been sent.");
      } else {
        await auth.login(email, password);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Pulse could not complete this action.");
    } finally {
      setBusy(false);
    }
  }

  const actionLabel = mode === "register" ? "Create Account" : mode === "recover" ? "Send Recovery" : "Log In";

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={screenStyles.screen}>
      <View style={{ paddingTop: 52 }}>
        <Text style={screenStyles.title}>Pulse</Text>
        <Text style={screenStyles.subtitle}>Sign in to PulseSoc.com to use your feed, reels, videos, messages, notifications, and profile.</Text>
        {mode === "register" ? (
          <>
            <TextInput value={fullName} onChangeText={setFullName} placeholder="Full name" placeholderTextColor={colors.muted} style={screenStyles.input} />
            <TextInput value={username} onChangeText={setUsername} autoCapitalize="none" placeholder="Username" placeholderTextColor={colors.muted} style={screenStyles.input} />
          </>
        ) : null}
        <TextInput value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="Email or username" placeholderTextColor={colors.muted} style={screenStyles.input} />
        {mode !== "recover" ? (
          <TextInput value={password} onChangeText={setPassword} secureTextEntry placeholder="Password" placeholderTextColor={colors.muted} style={screenStyles.input} />
        ) : null}
        {message ? <Text style={screenStyles.error}>{message}</Text> : null}
        <TouchableOpacity disabled={busy} onPress={submit} style={screenStyles.button}>
          {busy ? <ActivityIndicator color="#06111f" /> : <Text style={screenStyles.buttonText}>{actionLabel}</Text>}
        </TouchableOpacity>
        <TouchableOpacity onPress={() => setMode(mode === "register" ? "login" : "register")} style={screenStyles.secondaryButton}>
          <Text style={screenStyles.secondaryButtonText}>{mode === "register" ? "Already have an account?" : "Create an account"}</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => setMode(mode === "recover" ? "login" : "recover")} style={screenStyles.secondaryButton}>
          <Text style={screenStyles.secondaryButtonText}>{mode === "recover" ? "Back to login" : "Recover password"}</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}
