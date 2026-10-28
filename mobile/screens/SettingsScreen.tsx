// Settings — trading control (pause/resume), limits, devices, account, panic shortcut.

import React, { useCallback, useState } from "react";
import { View, Text, ScrollView, StyleSheet, TouchableOpacity, Alert, TextInput } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import * as LocalAuthentication from "expo-local-authentication";
import { useUIStore, useDevicesStore, useAuthStore } from "../store/index";
import { Colors, MONO, radius, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { formatUsd, formatDateTime } from "../utils/format";
import { Limit } from "../types/api";
import { Card, EmptyState, Icon, ScreenHeader, SectionTitle, StateChip } from "../components/ui";

export const SettingsScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const navigation = useNavigation<any>();
  const { mode, paused, setStatus } = useUIStore();
  const { devices, setDevices } = useDevicesStore();
  const { logout, serverUrl } = useAuthStore();
  const [limits, setLimits] = useState<Limit[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const loadLimits = async () => {
    try {
      const resp = await api.getLimits();
      if (resp.ok && resp.data) setLimits(resp.data);
    } catch {
      // ignore
    }
  };

  const loadDevices = async () => {
    try {
      const resp = await api.getDevices();
      if (resp.ok && resp.data?.devices) setDevices(resp.data.devices);
    } catch {
      // ignore
    }
  };

  useFocusEffect(
    useCallback(() => {
      loadLimits();
      loadDevices();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
  );

  const handleSaveLimit = async (limit: Limit) => {
    const val = parseFloat(editValue);
    if (isNaN(val)) {
      Alert.alert("Error", "Invalid amount");
      return;
    }
    const authenticated = await LocalAuthentication.authenticateAsync({ promptMessage: "Confirm limit change" });
    if (!authenticated.success) {
      Alert.alert("Error", "Biometric authentication failed");
      return;
    }
    try {
      const resp = await api.putLimits({ [limit.name]: val }, "biometric-confirmed");
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
      if (!resp.ok) {
        Alert.alert("Error", resp.error?.message || "Request failed");
        return;
      }
      // Wait for server state (no optimistic updates for trading controls).
      const s = await api.getStatus();
      if (s.ok && s.data) setStatus(s.data);
    } catch {
      Alert.alert("Error", "Network error");
    }
  };

  const confirmLogout = () => {
    Alert.alert("Log out", "Sign out of this device?", [
      { text: "Cancel", style: "cancel" },
      { text: "Log out", style: "destructive", onPress: () => void logout() },
    ]);
  };

  return (
    <View style={styles.container}>
      <ScreenHeader title="Settings" mode={mode} paused={paused} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <SectionTitle title="Trading" />
        <Card>
          <View style={styles.rowBetween}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>{paused ? "Trading is paused" : "Trading is active"}</Text>
              <Text style={styles.rowSub}>
                {paused ? "The agent will not create new proposals." : "The agent can create new proposals."}
              </Text>
            </View>
            <StateChip label={paused ? "PAUSED" : "ACTIVE"} tone={paused ? "alert" : "long"} />
          </View>
          <TouchableOpacity
            style={[styles.primaryBtn, paused ? { backgroundColor: c.long } : { backgroundColor: c.alert }]}
            onPress={handlePauseResume}
          >
            <Text style={styles.primaryBtnText}>{paused ? "Resume trading" : "Pause trading"}</Text>
          </TouchableOpacity>
        </Card>

        <SectionTitle title="Limits" />
        <Card style={{ paddingVertical: 4 }}>
          {limits.length === 0 ? (
            <EmptyState title="No limits loaded" />
          ) : (
            limits.map((limit, i) => {
              const pct = limit.hard_max > 0 ? Math.min(100, (limit.value / limit.hard_max) * 100) : 0;
              const isEditing = editing === limit.name;
              return (
                <View key={limit.name} style={[styles.limitRow, i > 0 && styles.rowBorder]}>
                  <View style={styles.rowBetween}>
                    <Text style={styles.rowTitle}>{limit.name.replace(/_/g, " ")}</Text>
                    <Text style={styles.mono}>{formatUsd(limit.value)}</Text>
                  </View>
                  <View style={styles.track}>
                    <View style={[styles.fill, { width: `${pct}%` }]} />
                  </View>
                  <Text style={styles.rowSub}>Hard max {formatUsd(String(limit.hard_max))}</Text>
                  {isEditing ? (
                    <View style={styles.editRow}>
                      <TextInput
                        style={styles.input}
                        value={editValue}
                        onChangeText={setEditValue}
                        keyboardType="numeric"
                        placeholderTextColor={c.placeholder}
                        keyboardAppearance={c.isDark ? "dark" : "light"}
                        autoFocus
                      />
                      <TouchableOpacity style={[styles.smallBtn, { backgroundColor: c.primary }]} onPress={() => handleSaveLimit(limit)}>
                        <Text style={[styles.smallBtnText, { color: c.onAccent }]}>Save</Text>
                      </TouchableOpacity>
                      <TouchableOpacity style={styles.smallBtn} onPress={() => setEditing(null)}>
                        <Text style={styles.smallBtnText}>Cancel</Text>
                      </TouchableOpacity>
                    </View>
                  ) : (
                    <TouchableOpacity
                      style={styles.smallBtn}
                      onPress={() => {
                        setEditing(limit.name);
                        setEditValue(String(limit.value));
                      }}
                    >
                      <Text style={styles.smallBtnText}>Change</Text>
                    </TouchableOpacity>
                  )}
                </View>
              );
            })
          )}
        </Card>

        <SectionTitle title="Devices" />
        <Card style={{ paddingVertical: 4 }}>
          {devices.length === 0 ? (
            <EmptyState title="No paired devices" />
          ) : (
            devices.map((d, i) => (
              <View key={d.id} style={[styles.rowBetween, styles.deviceRow, i > 0 && styles.rowBorder]}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowTitle}>{d.name}</Text>
                  <Text style={styles.rowSub}>
                    {d.last_seen ? `Last seen ${formatDateTime(new Date(d.last_seen * 1000).toISOString())}` : "Never seen"}
                  </Text>
                </View>
                <StateChip label={d.revoked_at ? "REVOKED" : "ACTIVE"} tone={d.revoked_at ? "muted" : "long"} />
              </View>
            ))
          )}
        </Card>

        <SectionTitle title="Account" />
        <Card>
          <Text style={styles.rowSub}>Server</Text>
          <Text style={[styles.mono, { marginTop: 2 }]} numberOfLines={1}>
            {serverUrl}
          </Text>
          <TouchableOpacity style={[styles.outlineBtn, { marginTop: 14 }]} onPress={confirmLogout}>
            <Icon name="logout" size={18} color={c.text} />
            <Text style={styles.outlineBtnText}>Log out</Text>
          </TouchableOpacity>
        </Card>

        <TouchableOpacity style={styles.panic} onPress={() => navigation.navigate("Panic")}>
          <Icon name="panic" size={20} color={c.danger} />
          <Text style={styles.panicText}>Open Panic controls</Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
};

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: c.background },
    content: { paddingHorizontal: 16, paddingBottom: 40 },
    rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
    rowTitle: { color: c.text, fontSize: 15, fontWeight: "700", textTransform: "capitalize" },
    rowSub: { color: c.textSecondary, fontSize: 12, marginTop: 2 },
    mono: { color: c.text, fontFamily: MONO, fontSize: 14, fontWeight: "700" },
    rowBorder: { borderTopWidth: 1, borderTopColor: c.border },
    primaryBtn: { marginTop: 14, minHeight: 48, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
    primaryBtnText: { color: c.onAccent, fontSize: 15, fontWeight: "800" },
    limitRow: { paddingVertical: 14, gap: 6 },
    track: { height: 6, borderRadius: 3, backgroundColor: c.track, overflow: "hidden" },
    fill: { height: 6, backgroundColor: c.primary },
    editRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6 },
    input: {
      flex: 1,
      minHeight: 44,
      borderWidth: 1,
      borderColor: c.border,
      borderRadius: radius.sm,
      paddingHorizontal: 12,
      color: c.text,
      backgroundColor: c.inputBackground,
      fontFamily: MONO,
    },
    smallBtn: {
      alignSelf: "flex-start",
      minHeight: 44,
      paddingHorizontal: 16,
      borderRadius: radius.sm,
      backgroundColor: c.surfaceAlt,
      justifyContent: "center",
      marginTop: 4,
    },
    smallBtnText: { color: c.primary, fontSize: 14, fontWeight: "700" },
    deviceRow: { paddingVertical: 14 },
    outlineBtn: {
      flexDirection: "row",
      gap: 8,
      minHeight: 48,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.border,
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: c.surfaceAlt,
    },
    outlineBtnText: { color: c.text, fontSize: 15, fontWeight: "700" },
    panic: {
      marginTop: 24,
      flexDirection: "row",
      gap: 8,
      minHeight: 52,
      borderRadius: radius.md,
      borderWidth: 1.5,
      borderColor: c.danger,
      backgroundColor: c.shortSoft,
      alignItems: "center",
      justifyContent: "center",
    },
    panicText: { color: c.danger, fontSize: 15, fontWeight: "800" },
  });
