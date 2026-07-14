import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { useEffect, useRef, useState } from "react";
import { Alert, Linking, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { signIn, useAuth } from "../session/auth";
import { createQaSimulatorLocalSession, isQaSimulatorAutoLoginEnabled, tryHandleQaSimulatorAuthUrl } from "../session/qaSimulatorAuth";
import { colors } from "../theme/colors";
import { AuthStackParamList } from "../navigation/types";

export function LoginScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<AuthStackParamList>>();
  const { setAuthState } = useAuth();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const qaBootstrapStarted = useRef(false);

  useEffect(() => {
    let mounted = true;

    async function handleQaUrl(url: string | null) {
      if (!url) return;
      const result = await tryHandleQaSimulatorAuthUrl(url).catch(() => null);
      if (!mounted || !result?.handled || !result.authState) return;
      setAuthState(result.authState);
    }

    Linking.getInitialURL().then(handleQaUrl).catch(() => undefined);
    const subscription = Linking.addEventListener("url", (event) => {
      handleQaUrl(event.url).catch(() => undefined);
    });
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, [setAuthState]);

  useEffect(() => {
    if (!isQaSimulatorAutoLoginEnabled() || qaBootstrapStarted.current) return;
    let mounted = true;
    qaBootstrapStarted.current = true;
    setLoading(true);
    createQaSimulatorLocalSession()
      .then((state) => {
        if (mounted && state.status === "signedIn") setAuthState(state);
      })
      .catch(() => undefined)
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [setAuthState]);

  async function submit() {
    setLoading(true);
    try {
      setAuthState(await signIn(identifier.trim(), password));
    } catch (error) {
      Alert.alert("Login failed", error instanceof Error ? error.message : "Unable to sign in.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={styles.root}>
      <Text style={styles.brand}>PulseSoc</Text>
      <Text style={styles.copy}>Sign in with your existing PulseSoc account. Your profile, followers, content, messages, subscriptions, and settings stay with the same account.</Text>
      <TextInput
        accessibilityLabel="Email or username"
        autoCapitalize="none"
        keyboardType="email-address"
        placeholder="Email or username"
        placeholderTextColor={colors.muted}
        style={styles.input}
        testID="login-identifier"
        value={identifier}
        onChangeText={setIdentifier}
      />
      <TextInput
        accessibilityLabel="Password"
        placeholder="Password"
        placeholderTextColor={colors.muted}
        secureTextEntry
        style={styles.input}
        testID="login-password"
        value={password}
        onChangeText={setPassword}
      />
      <Pressable accessibilityRole="button" accessibilityLabel="Sign in" testID="login-submit" style={styles.button} onPress={submit} disabled={loading}>
        <Text style={styles.buttonText}>{loading ? "Signing in" : "Sign in"}</Text>
      </Pressable>
      <Pressable accessibilityRole="button" onPress={() => navigation.navigate("AccountRecovery")}>
        <Text style={styles.link}>Forgot password or need email verification?</Text>
      </Pressable>
      <Pressable accessibilityRole="button" onPress={() => navigation.navigate("Signup")}>
        <Text style={styles.secondaryLink}>New to PulseSoc? Create an account</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    justifyContent: "center",
    gap: 14,
    padding: 22,
    backgroundColor: colors.background
  },
  brand: {
    color: colors.text,
    fontSize: 38,
    fontWeight: "900"
  },
  copy: {
    color: colors.muted,
    fontSize: 16,
    lineHeight: 23,
    marginBottom: 8
  },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    minHeight: 52,
    paddingHorizontal: 14
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 52,
    justifyContent: "center"
  },
  buttonText: {
    color: "#08110f",
    fontWeight: "800"
  },
  link: {
    color: colors.accentStrong,
    fontWeight: "700",
    textAlign: "center"
  },
  secondaryLink: {
    color: colors.muted,
    fontWeight: "700",
    textAlign: "center"
  }
});
