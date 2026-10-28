// Live — fixtures list with Live / Upcoming switch. Tap a fixture for p_fair vs market detail.

import React, { useCallback, useState } from "react";
import { View, Text, FlatList, StyleSheet, TouchableOpacity, RefreshControl } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { useUIStore } from "../store/index";
import { Colors, MONO, radius, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { formatDateTime } from "../utils/format";
import { FixtureSummary } from "../types/api";
import { EmptyState, ScreenHeader, Segmented, StateChip } from "../components/ui";

type Tab = "live" | "upcoming";
const LIVE_REFRESH_MS = 10000;

export const LiveScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const navigation = useNavigation<any>();
  const { mode, paused } = useUIStore();
  const [tab, setTab] = useState<Tab>("live");
  const [fixtures, setFixtures] = useState<FixtureSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.getFixtures(tab);
      if (r.ok && r.data) setFixtures(r.data);
    } catch {
      // keep the last list
    } finally {
      setLoaded(true);
    }
  }, [tab]);

  useFocusEffect(
    useCallback(() => {
      setLoaded(false);
      setFixtures([]);
      load();
      if (tab !== "live") return;
      const t = setInterval(load, LIVE_REFRESH_MS);
      return () => clearInterval(t);
    }, [load, tab])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const renderItem = ({ item }: { item: FixtureSummary }) => {
    const isLive = item.state === "live";
    const quotes = item.markets.filter((m) => m.best_bid_cents != null || m.best_ask_cents != null).slice(0, 3);
    return (
      <TouchableOpacity
        activeOpacity={0.85}
        style={styles.card}
        onPress={() => navigation.navigate("FixtureDetail", { id: item.id })}
      >
        <View style={styles.cardTop}>
          {isLive ? (
            <StateChip label={item.minute != null ? `LIVE · ${item.minute}′` : "LIVE"} tone="short" />
          ) : (
            <StateChip label={formatDateTime(item.kickoff)} tone="neutral" />
          )}
          {item.league ? (
            <Text style={styles.league} numberOfLines={1}>
              {item.league}
            </Text>
          ) : null}
        </View>

        <View style={styles.teamRow}>
          <Text style={styles.team} numberOfLines={1}>
            {item.home_team ?? "?"}
          </Text>
          <Text style={styles.score}>{item.home_score ?? (isLive ? 0 : "")}</Text>
        </View>
        <View style={styles.teamRow}>
          <Text style={styles.team} numberOfLines={1}>
            {item.away_team ?? "?"}
          </Text>
          <Text style={styles.score}>{item.away_score ?? (isLive ? 0 : "")}</Text>
        </View>

        {quotes.length > 0 && (
          <View style={styles.quotes}>
            {quotes.map((m) => (
              <View key={m.market_id} style={styles.quote}>
                <Text style={styles.quoteLabel}>BID / ASK</Text>
                <Text style={styles.quoteValue}>
                  {m.best_bid_cents ?? "—"} / {m.best_ask_cents ?? "—"}¢
                </Text>
              </View>
            ))}
          </View>
        )}
      </TouchableOpacity>
    );
  };

  return (
    <View style={styles.container}>
      <ScreenHeader title="Live" subtitle="Matches the agent is watching" mode={mode} paused={paused} />
      <View style={styles.segmentWrap}>
        <Segmented
          options={[
            { label: "Live now", value: "live" },
            { label: "Upcoming", value: "upcoming" },
          ]}
          value={tab}
          onChange={setTab}
        />
      </View>
      <FlatList
        data={fixtures}
        keyExtractor={(f) => f.id}
        renderItem={renderItem}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={c.textSecondary} />}
        ListEmptyComponent={
          loaded ? (
            <EmptyState
              title={tab === "live" ? "No live matches right now" : "No upcoming matches"}
              hint="Pull down to refresh."
            />
          ) : null
        }
      />
    </View>
  );
};

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: c.background },
    segmentWrap: { paddingHorizontal: 16, paddingBottom: 8 },
    list: { paddingHorizontal: 16, paddingBottom: 32, paddingTop: 4 },
    card: {
      backgroundColor: c.surface,
      borderRadius: radius.lg,
      borderWidth: 1,
      borderColor: c.border,
      padding: 14,
      marginBottom: 10,
    },
    cardTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 10 },
    league: { color: c.textSecondary, fontSize: 12, flexShrink: 1 },
    teamRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 3 },
    team: { color: c.text, fontSize: 16, fontWeight: "700", flex: 1, marginRight: 8 },
    score: { color: c.text, fontFamily: MONO, fontSize: 20, fontWeight: "800" },
    quotes: { flexDirection: "row", gap: 8, marginTop: 12 },
    quote: { flex: 1, backgroundColor: c.surfaceAlt, borderRadius: radius.sm, padding: 8 },
    quoteLabel: { color: c.textMuted, fontSize: 9, fontWeight: "800", letterSpacing: 0.6 },
    quoteValue: { color: c.text, fontFamily: MONO, fontSize: 13, fontWeight: "700", marginTop: 2 },
  });
