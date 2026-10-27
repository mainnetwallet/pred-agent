// Pairing screen — scan QR or enter code, device naming, token → secure store (kit/08_MOBILE_APP.md)

import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Alert,
  ActivityIndicator,
} from "react-native";
import { useAuthStore } from "../store/index";
import { api } from "../services/api";

export const PairingScreen: React.FC = () => {
  const [code, setCode] = useState("");
  const [deviceName, setDeviceName] = useState("");
  const [loading, setLoading] = useState(false);
  const { setToken } = useAuthStore();

  const handlePair = async () => {
    if (!code.trim()) {
      Alert.alert("Error", "Please enter a pairing code");
      return;
    }
    setLoading(true);
    try {
      const resp = await api.pair(code, deviceName || "my-phone", "");
      if (resp.ok && resp.data?.device_token) {
        setToken(resp.data.device_token);
        // Navigate to home would be handled by navigation
      } else {
        Alert.alert("Error", resp.error?.message || "Pairing failed");
      }
    } catch {
      Alert.alert("Error", "Network error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Pair Device</Text>
      <Text style={styles.subtitle}>
        Enter the pairing code from the server (scripts/pair_device.py)
      </Text>
      <TextInput
        style={styles.input}
        placeholder="Pairing code"
        value={code}
        onChangeText={setCode}
        autoCapitalize="none"
      />
      <TextInput
        style={styles.input}
        placeholder="Device name (e.g. my-phone)"
        value={deviceName}
        onChangeText={setDeviceName}
      />
      <TouchableOpacity style={styles.button} onPress={handlePair} disabled={loading}>
        <Text style={styles.buttonText}>
          {loading ? "Pairing..." : "Pair Device"}
        </Text>
      </TouchableOpacity>
      <Text style={styles.hint}>
        Token is stored in secure storage (Keychain/Keystore) behind biometrics.
      </Text>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
  },
  title: {
    fontSize: 24,
    fontWeight: "600",
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 14,
    color: "#666",
    marginBottom: 20,
  },
  input: {
    borderWidth: 1,
    borderColor: "#ddd",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    marginBottom: 12,
  },
  button: {
    backgroundColor: "#007AFF",
    borderRadius: 8,
    padding: 14,
    alignItems: "center",
  },
  buttonText: {
    color: "#fff",
    fontWeight: "600",
    fontSize: 16,
  },
  hint: {
    marginTop: 20,
    fontSize: 12,
    color: "#666",
    lineHeight: 18,
  },
});
