// Proposals screen — card stack with Approve (biometric), Change amount, Reject,
// countdown to expiry (kit/08_MOBILE_APP.md)

import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  TouchableOpacity,
  Alert,
  TextInput,
} from "react-native";
import * as LocalAuthentication from "expo-local-authentication";
import { useUIStore, useProposalsStore } from "../store/index";
import { Colors, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { StatusBanner, OfflineBadge } from "../components/common";
import { formatEdge, formatDateTime } from "../utils/format";

export const ProposalsScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const { proposals, setProposals } = useProposalsStore();
  const { mode, paused } = useUIStore();
  const [refreshing, setRefreshing] = useState(false);
  const [changingAmount, setChangingAmount] = useState<string | null>(null);
  const [newAmount, setNewAmount] = useState("");

  useEffect(() => {
    loadProposals();
  }, []);

  const loadProposals = async () => {
    setRefreshing(true);
    try {
      const resp = await api.getProposals("pending");
      if (resp.ok && resp.data) {
        setProposals(resp.data);
      }
    } catch {
      // handled offline
    } finally {
      setRefreshing(false);
    }
  };

  const handleApprove = async (proposal: any) => {
    // Re-fetch proposal right before confirm (kit/08_MOBILE_APP.md rule)
    try {
      const detailResp = await api.getProposals("pending");
      const current = detailResp.ok
        ? detailResp.data?.find((p: any) => p.id === proposal.id)
        : null;
      if (!current || current.status !== "PROPOSED") {
        Alert.alert("Error", "Proposal is no longer available");
        await loadProposals();
        return;
      }

      // Biometric confirmation for sensitive action
      const authenticated = await LocalAuthentication.authenticateAsync({
        promptMessage: "Confirm trade approval",
      });
      if (!authenticated.success) {
        Alert.alert("Error", "Biometric authentication failed");
        return;
      }

      const amount = changingAmount === proposal.id ? newAmount : undefined;
      const resp = await api.approveProposal(proposal.id, amount ? { amount_usd: amount } : {}, "biometric-confirmed");
      if (resp.ok) {
        Alert.alert("Approved", "Order submitted for execution");
        await loadProposals();
      } else {
        Alert.alert("Error", resp.error?.message || "Approval failed");
      }
    } catch {
      Alert.alert("Error", "Network error");
    }
  };

  const handleReject = async (proposal: any) => {
    const authenticated = await LocalAuthentication.authenticateAsync({
      promptMessage: "Confirm rejection",
    });
    if (!authenticated.success) return;
    try {
      const resp = await api.rejectProposal(proposal.id);
      if (resp.ok) {
        await loadProposals();
      } else {
        Alert.alert("Error", resp.error?.message || "Reject failed");
      }
    } catch {
      Alert.alert("Error", "Network error");
    }
  };

  const renderItem = ({ item }: { item: any }) => {
    const isChanging = changingAmount === item.id;
    return (
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.modeTag}>
            {item.advisory ? "[ADVISORY] " : ""}{item.mode}
          </Text>
          <Text style={styles.expiry}>Expires: {formatDateTime(item.expires_at)}</Text>
        </View>
        <Text style={styles.fixture}>{item.fixture_id}</Text>
        <View style={styles.detailRow}>
          <Text style={styles.label}>Side:</Text>
          <Text style={styles.value}>{item.side.toUpperCase()}</Text>
        </View>
        <View style={styles.detailRow}>
          <Text style={styles.label}>Price:</Text>
          <Text style={styles.value}>@{item.price_cents}c</Text>
        </View>
        <View style={styles.detailRow}>
          <Text style={styles.label}>Qty:</Text>
          <Text style={styles.value}>{item.quantity}</Text>
        </View>
        <View style={styles.detailRow}>
          <Text style={styles.label}>Amount:</Text>
          <Text style={styles.value}>${item.amount_usd}</Text>
        </View>
        <View style={styles.detailRow}>
          <Text style={styles.label}>Est Fee:</Text>
          <Text style={styles.value}>${item.est_fee_usd}</Text>
        </View>
        <View style={styles.detailRow}>
          <Text style={styles.label}>p_fair:</Text>
          <Text style={styles.value}>{(parseFloat(item.p_fair) * 100).toFixed(0)}%</Text>
        </View>
        <View style={styles.detailRow}>
          <Text style={styles.label}>Edge:</Text>
          <Text style={styles.value}>{formatEdge(item.edge_pp)}</Text>
        </View>
        <View style={styles.detailRow}>
          <Text style={styles.label}>Max suggested:</Text>
          <Text style={styles.value}>${item.max_suggested_usd}</Text>
        </View>
        {item.reasons?.length > 0 && (
          <View style={styles.reasons}>
            <Text style={styles.reasonTitle}>Why:</Text>
            {item.reasons.map((r: string, i: number) => (
              <Text key={i} style={styles.reasonText}>
                • {r}
              </Text>
            ))}
          </View>
        )}
        {item.risks?.length > 0 && (
          <View style={styles.risks}>
            <Text style={styles.reasonTitle}>Risks:</Text>
            {item.risks.map((r: string, i: number) => (
              <Text key={i} style={styles.reasonText}>
                • {r}
              </Text>
            ))}
          </View>
        )}
        {isChanging ? (
          <View style={styles.amountInputRow}>
            <TextInput
              placeholderTextColor={c.placeholder}
              keyboardAppearance={c.isDark ? "dark" : "light"}
              style={styles.amountInput}
              placeholder="New amount (USD)"
              value={newAmount}
              onChangeText={setNewAmount}
              keyboardType="numeric"
            />
            <TouchableOpacity
              style={styles.smallButton}
              onPress={() => {
                if (newAmount) {
                  handleApprove({ ...item, amount_usd: newAmount });
                }
                setChangingAmount(null);
                setNewAmount("");
              }}
            >
              <Text style={styles.smallButtonText}>OK</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.smallButton}
              onPress={() => {
                setChangingAmount(null);
                setNewAmount("");
              }}
            >
              <Text style={styles.smallButtonText}>Cancel</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <View style={styles.actions}>
            <TouchableOpacity
              style={[styles.button, styles.buttonReject]}
              onPress={() => handleReject(item)}
            >
              <Text style={styles.buttonText}>Reject</Text>
            </TouchableOpacity>
            {changingAmount === item.id ? (
              <TouchableOpacity
                style={[styles.button, styles.buttonOutline]}
                onPress={() => {
                  setChangingAmount(null);
                  setNewAmount("");
                }}
              >
                <Text style={[styles.buttonText, styles.buttonOutlineText]}>Cancel amount</Text>
              </TouchableOpacity>
            ) : (
              <TouchableOpacity
                style={[styles.button, styles.buttonApprove]}
                onPress={() => handleApprove(item)}
              >
                <Text style={styles.buttonText}>Approve</Text>
              </TouchableOpacity>
            )}
            <TouchableOpacity
              style={[styles.button, styles.buttonOutline]}
              onPress={() => setChangingAmount(item.id)}
            >
              <Text style={[styles.buttonText, styles.buttonOutlineText]}>Change amount</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    );
  };

  return (
    <View style={styles.container}>
      <StatusBanner mode={mode} paused={paused} />
      <View style={styles.content}>
        <OfflineBadge stale={!paused} lastUpdate={null} />
        <FlatList
          data={proposals}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          refreshControl={
            <ReactNativeRefreshControl refreshing={refreshing} onRefresh={loadProposals} />
          }
        />
      </View>
    </View>
  );
};

const ReactNativeRefreshControl = require("react-native").RefreshControl;

const makeStyles = (c: Colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: c.background,
  },
  content: {
    padding: 16,
  },
  card: {
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
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 12,
  },
  modeTag: {
    fontSize: 11,
    fontWeight: "600",
    color: c.textSecondary,
    textTransform: "uppercase",
  },
  expiry: {
    fontSize: 11,
    color: c.textMuted,
  },
  fixture: {
    color: c.text,
    fontSize: 16,
    fontWeight: "700",
    marginBottom: 12,
  },
  detailRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginVertical: 3,
  },
  label: {
    fontSize: 13,
    color: c.textSecondary,
  },
  value: {
    color: c.text,
    fontSize: 13,
    fontWeight: "600",
    fontFamily: "monospace",
  },
  reasons: {
    marginTop: 10,
  },
  reasonTitle: {
    fontSize: 11,
    fontWeight: "600",
    color: c.long,
    marginBottom: 4,
  },
  reasonText: {
    fontSize: 12,
    color: c.text,
    marginBottom: 2,
  },
  risks: {
    marginTop: 10,
  },
  actions: {
    flexDirection: "row",
    marginTop: 14,
  },
  button: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: "center",
    marginHorizontal: 4,
  },
  buttonApprove: {
    backgroundColor: c.long,
  },
  buttonReject: {
    backgroundColor: c.short,
  },
  buttonOutline: {
    backgroundColor: c.surface,
    borderWidth: 1,
    borderColor: c.border,
  },
  buttonOutlineText: {
    color: c.text,
  },
  buttonText: {
    color: c.onAccent,
    fontWeight: "600",
    fontSize: 14,
  },
  amountInputRow: {
    flexDirection: "row",
    marginTop: 14,
  },
  amountInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: c.border,
    borderRadius: 8,
    padding: 10,
    fontSize: 14,
    marginRight: 8,
    color: c.text,
    backgroundColor: c.inputBackground,
  },
  smallButton: {
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: c.surfaceAlt,
    borderRadius: 8,
    marginHorizontal: 4,
  },
  smallButtonText: {
    fontSize: 14,
    fontWeight: "600",
    color: c.primary,
  },
});
