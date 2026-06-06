import React, { useState } from "react";
import { Text, TextInput, TouchableOpacity, View } from "react-native";
import { useAuth } from "../auth/AuthProvider";
import { styles } from "../styles";

export function AuthScreen() {
  const auth = useAuth();
  const [mode, setMode] = useState<"login" | "register" | "recover">("login");
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");

  async function submit() {
    try {
      if (mode === "recover") {
        setMessage("Requesting recovery...");
        await auth.recover(email);
        setMessage("If an account exists, password recovery has been sent.");
        return;
      }
      if (mode === "register") {
        setMessage("Creating account...");
        await auth.register({ full_name: fullName, username, email, password });
        return;
      }
      setMessage("Signing in...");
      await auth.login(email, password);
    } catch (error: any) {
      setMessage(error.message);
    }
  }

  return (
    <View style={styles.auth}>
      <Text style={styles.logo}>Pulse</Text>
      <View style={styles.segmented}>
        <TouchableOpacity style={[styles.segment, mode === "login" && styles.segmentActive]} onPress={() => setMode("login")}><Text style={styles.segmentText}>Login</Text></TouchableOpacity>
        <TouchableOpacity style={[styles.segment, mode === "register" && styles.segmentActive]} onPress={() => setMode("register")}><Text style={styles.segmentText}>Register</Text></TouchableOpacity>
        <TouchableOpacity style={[styles.segment, mode === "recover" && styles.segmentActive]} onPress={() => setMode("recover")}><Text style={styles.segmentText}>Recover</Text></TouchableOpacity>
      </View>
      {mode === "register" && (
        <>
          <TextInput style={styles.input} placeholder="Full name" value={fullName} onChangeText={setFullName} />
          <TextInput style={styles.input} autoCapitalize="none" placeholder="Username" value={username} onChangeText={setUsername} />
        </>
      )}
      <TextInput style={styles.input} autoCapitalize="none" keyboardType="email-address" placeholder="Email" value={email} onChangeText={setEmail} />
      {mode !== "recover" && <TextInput style={styles.input} secureTextEntry placeholder="Password" value={password} onChangeText={setPassword} />}
      <TouchableOpacity style={styles.primaryButton} onPress={submit}>
        <Text style={styles.primaryButtonText}>{mode === "register" ? "Create account" : mode === "recover" ? "Send recovery" : "Log in"}</Text>
      </TouchableOpacity>
      {!!message && <Text style={styles.muted}>{message}</Text>}
    </View>
  );
}
