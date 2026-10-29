// Login screen — server URL + username + password (fixed in the server's env).

import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Alert,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { useAuthStore } from "../store/index";
import { Colors, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { wsClient } from "../services/websocket";
import { DEFAULT_SERVER_URL, DEFAULT_USERNAME } from "../utils/defaults";
import { Logo } from "../components/Logo";

export const LoginScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const { serverUrl, setServerUrl, setToken } = useAuthStore();
  const [url, setUrl] = useState(serverUrl || DEFAULT_SERVER_URL);
  // Storage hydrates asynchronously; keep the field in sync once it does.
  useEffect(() => setUrl(serverUrl || DEFAULT_SERVER_URL), [serverUrl]);
  const [username, setUsername] = useState(DEFAULT_USERNAME);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    const base = url.trim();
    if (!/^https?:\/\//i.test(base)) {
      Alert.alert("Server URL", "Enter the full URL, e.g. https://your-app.onrender.com");
      return;
    }
    if (!username.trim() || !password) {
      Alert.alert("Login", "Enter username and password");
      return;
    }
    setLoading(true);
    try {
      api.setBase(base);
      wsClient.setUrl(base);
      const resp = await api.login(username.trim(), password);
      if (resp.ok && resp.data?.token) {
        // Set the token on the clients first: the Home screen fires its requests as soon as it
        // mounts, before App's effect has synced the token.
        api.setToken(resp.data.token);
        wsClient.setToken(resp.data.token);
        setServerUrl(base);
        setToken(resp.data.token);
        setPassword("");
      } else {
        Alert.alert("Login failed", resp.error?.message || "Wrong username or password");
      }
    } catch {
      Alert.alert("Error", "Could not reach the server. Check the URL and your connection.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <View style={styles.logo}>
        <Logo size={80} />
      </View>
      <Text style={styles.title}>PRED Agent</Text>
      <Text style={styles.subtitle}>Sign in to control your agent</Text>
      <TextInput
        placeholderTextColor={c.placeholder}
        keyboardAppearance={c.isDark ? "dark" : "light"}
        style={styles.input}
        placeholder="Server URL (https://...onrender.com)"
        value={url}
        onChangeText={setUrl}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
      />
      <TextInput
        placeholderTextColor={c.placeholder}
        keyboardAppearance={c.isDark ? "dark" : "light"}
        style={styles.input}
        placeholder="Username"
        value={username}
        onChangeText={setUsername}
        autoCapitalize="none"
        autoCorrect={false}
      />
      <TextInput
        placeholderTextColor={c.placeholder}
        keyboardAppearance={c.isDark ? "dark" : "light"}
        style={styles.input}
        placeholder="Password"
        value={password}
        onChangeText={setPassword}
        secureTextEntry
        autoCapitalize="none"
        onSubmitEditing={handleLogin}
      />
      <TouchableOpacity style={styles.button} onPress={handleLogin} disabled={loading}>
        <Text style={styles.buttonText}>{loading ? "Signing in..." : "Sign in"}</Text>
      </TouchableOpacity>
      <Text style={styles.hint}>
        The username and password are the ADMIN_USERNAME / ADMIN_PASSWORD set in the server
        environment. The session token is kept in secure storage (Keychain/Keystore).
      </Text>
    </KeyboardAvoidingView>
  );
};

const makeStyles = (c: Colors) => StyleSheet.create({
  container: { flex: 1, justifyContent: "center", padding: 24, backgroundColor: c.background },
  logo: { marginBottom: 20 },
  title: { color: c.text, fontSize: 34, fontWeight: "800", letterSpacing: -1, marginBottom: 4 },
  subtitle: { fontSize: 15, color: c.textSecondary, marginBottom: 28 },
  input: {
    borderWidth: 1,
    borderColor: c.border,
    borderRadius: 12,
    paddingHorizontal: 14,
    minHeight: 52,
    fontSize: 16,
    marginBottom: 12,
    color: c.text,
    backgroundColor: c.inputBackground,
  },
  button: { backgroundColor: c.primary, borderRadius: 12, minHeight: 52, alignItems: "center", justifyContent: "center", marginTop: 4 },
  buttonText: { color: c.onAccent, fontWeight: "800", fontSize: 16 },
  hint: { marginTop: 20, fontSize: 12, color: c.textMuted, lineHeight: 18 },
});
