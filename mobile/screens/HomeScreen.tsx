// Home screen — status strip (PAPER/LIVE + paused), balance, exposure, daily PnL,
// pending proposals count, live fixtures list (kit/08_MOBILE_APP.md)

import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  ActivityIndicator,
  TouchableOpacity,
  Alert,
} from "react-native";
import { useNavigation } from "@react-navigation/native";
import { useUIStore, useProposalsStore, useAuthStore } from "../store/index";
import { Colors, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { StatusBanner, MoneyDisplay, OfflineBadge } from "../components/common";
import { FixtureSummary } from "../types/api";

export const HomeScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const { setStatus, mode, paused, feedsOk, balance, exposure, dailyPnl } = useUIStore();
  const { proposals, setProposals } = useProposalsStore();
  const [refreshing, setRefreshing] = useState(false);
  const [fixtures, setFixtures] = useState<FixtureSummary[]>([]);
  const navigation = useNavigation<any>();
  const { logout } = useAuthStore();

  const confirmLogout = () => {
    Alert.alert("Log out", "Sign out of this device?", [
      { text: "Cancel", style: "cancel" },
      { text: "Log out", style: "destructive", onPress: () => void logout() },
    ]);
  };

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setRefreshing(true);
    try {
      const statusResp = await api.getStatus();
      if (statusResp.ok && statusResp.data) {
        setStatus(statusResp.data);
      }
      const proposalsResp = await api.getProposals("pending");
      if (proposalsResp.ok && proposalsResp.data) {
        setProposals(proposalsResp.data);
      }
      const fixturesResp = await api.getFixtures("live");
      if (fixturesResp.ok && fixturesResp.data) {
        setFixtures(fixturesResp.data);
      }
    } catch {
      // error handled by stale badge
    } finally {
      setRefreshing(false);
    }
  };

  const pendingCount = proposals.filter((p) => p.status === "PROPOSED").length;

  return (
    <View style={styles.container}>
      <StatusBanner mode={mode} paused={paused} />
      <View style={styles.content}>
        <View style={styles.navRow}>
          <TouchableOpacity style={styles.navButton} onPress={() => navigation.navigate("Chat")}>
            <Text style={styles.navText}>Chat</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.navButton} onPress={() => navigation.navigate("Rules")}>
            <Text style={styles.navText}>Rules</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.navButton} onPress={() => navigation.navigate("Proposals")}>
            <Text style={styles.navText}>Proposals</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.navButton} onPress={() => navigation.navigate("LimitsSettings")}>
            <Text style={styles.navText}>Limits</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.navButton} onPress={() => navigation.navigate("Panic")}>
            <Text style={styles.navText}>Panic</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.navButton} onPress={confirmLogout}>
            <Text style={styles.navText}>Log out</Text>
          </TouchableOpacity>
        </View>
        <View style={styles.statsRow}>
          <MoneyDisplay amount={balance} label="Balance" />
          <MoneyDisplay amount={exposure} label="Exposure" />
          <MoneyDisplay amount={dailyPnl} label="Daily PnL" />
        </View>
        <OfflineBadge stale={!feedsOk} lastUpdate={null} />
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Pending Proposals ({pendingCount})</Text>
          {proposals.length === 0 ? (
            <Text style={styles.empty}>No pending proposals</Text>
          ) : (
            <FlatList
              data={proposals.filter((p) => p.status === "PROPOSED")}
              keyExtractor={(item) => item.id}
              renderItem={({ item }) => (
                <View style={styles.proposalRow}>
                  <Text style={styles.proposalSide}>{item.side.toUpperCase()}</Text>
                  <Text style={styles.proposalPrice}>
                    @ {item.price_cents}c
                  </Text>
                  <Text style={styles.proposalAmount}>
                    ${item.amount_usd}
                  </Text>
                </View>
              )}
            />
          )}
        </View>
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Live Fixtures</Text>
          {refreshing && fixtures.length === 0 ? (
            <ActivityIndicator size="small" color={c.textSecondary} />
          ) : fixtures.length === 0 ? (
            <Text style={styles.empty}>No live fixtures right now</Text>
          ) : (
            <FlatList
              data={fixtures}
              keyExtractor={(item) => item.id}
              renderItem={({ item }) => (
                <View style={styles.fixtureRow}>
                  <Text style={styles.fixtureName}>
                    {item.home_team ?? "?"} vs {item.away_team ?? "?"}
                  </Text>
                  <Text style={styles.fixtureStatus}>
                    {item.home_score != null
                      ? `${item.home_score}–${item.away_score ?? 0}${item.minute != null ? ` · ${item.minute}′` : ""}`
                      : (item.game_status ?? "LIVE")}
                  </Text>
                </View>
              )}
            />
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
  navRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 16,
  },
  navButton: {
    borderWidth: 1,
    borderColor: c.border,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 12,
    backgroundColor: c.surface,
  },
  navText: {
    color: c.text,
    fontSize: 14,
    fontWeight: "600",
  },
  statsRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 16,
  },
  section: {
    marginBottom: 16,
  },
  sectionTitle: {
    color: c.text,
    fontSize: 16,
    fontWeight: "600",
    marginBottom: 8,
  },
  fixtureRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
  },
  fixtureName: {
    color: c.text,
    fontSize: 14,
    fontWeight: "500",
  },
  fixtureStatus: {
    fontSize: 12,
    color: c.textSecondary,
  },
  proposalRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
  },
  proposalSide: {
    color: c.text,
    fontSize: 13,
    fontWeight: "600",
  },
  proposalPrice: {
    fontSize: 13,
    color: c.textSecondary,
  },
  proposalAmount: {
    color: c.text,
    fontSize: 13,
    fontWeight: "600",
  },
  empty: {
    fontSize: 14,
    color: c.textSecondary,
    textAlign: "center",
    paddingVertical: 20,
  },
});
