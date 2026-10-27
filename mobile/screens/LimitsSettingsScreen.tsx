// Limits & settings screen — view/edit limits within bounds (biometric), pause, resume,
// devices list, revoke, notifications (kit/08_MOBILE_APP.md)

import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Alert,
  TextInput,
} from "react-native";
import * as LocalAuthentication from "expo-local-authentication";
import { useUIStore, useDevicesStore, useAuthStore } from "../store/index";
import { Colors, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { StatusBanner } from "../components/common";
import { formatUsd } from "../utils/format";

export const LimitsSettingsScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const { mode, paused } = useUIStore();
  const { devices, setDevices } = useDevicesStore();
  const { logout } = useAuthStore();
  const [limits, setLimits] = useState<any[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  useEffect(() => {
    loadLimits();
    loadDevices();
  }, []);

  const loadLimits = async () => {
    try {
      const resp = await api.getLimits();
      if (resp.ok && resp.data) {
        setLimits(resp.data);
      }
    } catch {
      // ignore
    }
  };

  const loadDevices = async () => {
    try {
      const resp = await api.getDevices();
      if (resp.ok && resp.data?.devices) {
        setDevices(resp.data.devices);
      }
    } catch {
      // ignore
    }
  };

  const handleSaveLimit = async (limit: any) => {
    // Biometric confirmation for sensitive action
    const authenticated = await LocalAuthentication.authenticateAsync({
      promptMessage: "Confirm limit change",
    });
    if (!authenticated.success) {
      Alert.alert("Error", "Biometric authentication failed");
      return;
    }

    const val = parseFloat(editValue);
    if (isNaN(val)) {
      Alert.alert("Error", "Invalid amount");
      return;
    }

    try {
      const resp = await api.putLimits(
        { [limit.name]: val },
        "biometric-confirmed"
      );
      if (resp.ok) {
        setEditing(null);
        setEditValue("");
        await loadLimits();
      } else {
        Alert.alert("Error", resp.error?.message || "Failed to update limit");
      }
    } catch {
      Alert.alert("Error", "Network error");
    }
  };

  const handlePauseResume = async () => {
    const authenticated = await LocalAuthentication.authenticateAsync({
      promptMessage: paused ? "Confirm resume" : "Confirm pause",
    });
    if (!authenticated.success) return;

    try {
      const resp = paused ? await api.resume("biometric-confirmed") : await api.pause("biometric-confirmed");
      if (resp.ok && resp.data) {
        // UI store updated elsewhere via WebSocket or refetch
      }
    } catch {
      Alert.alert("Error", "Network error");
    }
  };

  return (
    <View style={styles.container}>
      <StatusBanner mode={mode} paused={paused} />
      <View style={styles.content}>
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Limits</Text>
          {limits.map((limit) => (
            <View key={limit.name} style={styles.limitRow}>
              <View>
                <Text style={styles.limitName}>{limit.name}</Text>
                <Text style={styles.limitHard}>
                  Hard max: {formatUsd(String(limit.hard_max))}
                </Text>
              </View>
              {editing === limit.name ? (
                <View style={styles.editRow}>
                  <TextInput
                    placeholderTextColor={c.placeholder}
                    keyboardAppearance={c.isDark ? "dark" : "light"}
                    style={styles.editInput}
                    value={editValue}
                    onChangeText={setEditValue}
                    keyboardType="numeric"
                  />
                  <TouchableOpacity
                    style={styles.smallButton}
                    onPress={() => handleSaveLimit(limit)}
                  >
                    <Text style={styles.smallButtonText}>Save</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={styles.smallButton}
                    onPress={() => setEditing(null)}
                  >
                    <Text style={styles.smallButtonText}>Cancel</Text>
                  </TouchableOpacity>
                </View>
              ) : (
                <TouchableOpacity
                  style={styles.editButton}
                  onPress={() => {
                    setEditing(limit.name);
                    setEditValue(String(limit.value));
                  }}
                >
                  <Text style={styles.editButtonText}>Change</Text>
                </TouchableOpacity>
              )}
            </View>
          ))}
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Pause / Resume</Text>
          <TouchableOpacity
            style={[styles.actionButton, paused && styles.actionButtonActive]}
            onPress={handlePauseResume}
          >
            <Text style={styles.actionButtonText}>
              {paused ? "Resume Trading" : "Pause Trading"}
            </Text>
          </TouchableOpacity>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Account</Text>
          <TouchableOpacity style={styles.actionButton} onPress={logout}>
            <Text style={styles.actionButtonText}>Log out</Text>
          </TouchableOpacity>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Devices</Text>
          {devices.length === 0 ? (
            <Text style={styles.empty}>No paired devices</Text>
          ) : (
            devices.map((device) => (
              <View key={device.id} style={styles.deviceRow}>
                <Text style={styles.deviceName}>{device.name}</Text>
                <Text style={styles.deviceStatus}>
                  {device.revoked_at ? "Revoked" : "Active"}
                </Text>
              </View>
            ))
          )}
        </View>
      </View>
    </View>
  );
};

const makeStyles = (c: Colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: c.background,
  },
  content: {
    padding: 16,
  },
  section: {
    backgroundColor: c.surface,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    shadowColor: c.shadow,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  sectionTitle: {
    color: c.text,
    fontSize: 16,
    fontWeight: "600",
    marginBottom: 12,
  },
  limitRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
  },
  limitName: {
    color: c.text,
    fontSize: 14,
    fontWeight: "500",
  },
  limitHard: {
    fontSize: 12,
    color: c.textSecondary,
    marginTop: 2,
  },
  editRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 8,
  },
  editInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: c.border,
    borderRadius: 6,
    padding: 8,
    fontSize: 14,
    marginRight: 8,
    color: c.text,
    backgroundColor: c.inputBackground,
  },
  smallButton: {
    paddingVertical: 8,
    paddingHorizontal: 12,
    backgroundColor: c.surfaceAlt,
    borderRadius: 6,
    marginHorizontal: 4,
  },
  smallButtonText: {
    fontSize: 14,
    fontWeight: "600",
    color: c.primary,
  },
  editButton: {
    paddingVertical: 6,
    paddingHorizontal: 12,
    backgroundColor: c.surfaceAlt,
    borderRadius: 6,
  },
  editButtonText: {
    fontSize: 13,
    color: c.primary,
    fontWeight: "600",
  },
  actionButton: {
    backgroundColor: c.primary,
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: "center",
    marginVertical: 8,
  },
  actionButtonActive: {
    backgroundColor: c.short,
  },
  actionButtonText: {
    color: c.onAccent,
    fontWeight: "600",
    fontSize: 16,
  },
  deviceRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
    backgroundColor: c.surface,
  },
  deviceName: {
    color: c.text,
    fontSize: 14,
    fontWeight: "500",
  },
  deviceStatus: {
    fontSize: 12,
    color: c.textSecondary,
  },
  empty: {
    fontSize: 14,
    color: c.textSecondary,
    textAlign: "center",
    paddingVertical: 20,
  },
});
