// Login screen — server URL + username + password (fixed in the server's env).

import React, { useEffect, useState } from "react";
import {
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Alert,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { useAuthStore } from "../store/index";
import { api } from "../services/api";
import { wsClient } from "../services/websocket";
import { DEFAULT_SERVER_URL, DEFAULT_USERNAME } from "../utils/defaults";

export const LoginScreen: React.FC = () => {
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
      <Text style={styles.title}>PRED Agent</Text>
      <Text style={styles.subtitle}>Sign in to control your agent</Text>
      <TextInput
        style={styles.input}
        placeholder="Server URL (https://...onrender.com)"
        value={url}
        onChangeText={setUrl}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
      />
      <TextInput
        style={styles.input}
        placeholder="Username"
        value={username}
        onChangeText={setUsername}
        autoCapitalize="none"
        autoCorrect={false}
      />
      <TextInput
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

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", padding: 24 },
  title: { fontSize: 26, fontWeight: "700", marginBottom: 4 },
  subtitle: { fontSize: 14, color: "#666", marginBottom: 20 },
  input: {
    borderWidth: 1,
    borderColor: "#ddd",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    marginBottom: 12,
  },
  button: { backgroundColor: "#007AFF", borderRadius: 8, padding: 14, alignItems: "center" },
  buttonText: { color: "#fff", fontWeight: "600", fontSize: 16 },
  hint: { marginTop: 20, fontSize: 12, color: "#666", lineHeight: 18 },
});
