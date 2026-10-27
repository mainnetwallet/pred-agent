/**
 * ChatScreen — multi-turn chat with the PRED Agent.
 *
 * Phase 8 (D2): KeyboardAvoidingView, auto-scroll, quick chips,
 * loading + error + llm_not_configured states, clear chat,
 * card rendering (rule confirm/cancel with biometric X-Confirm gate).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { api } from "../services/api";
import { useNavigation } from "@react-navigation/native";
import { ChatCard } from "../types/api";

// ── Types ─────────────────────────────────────────────────────
interface LocalMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  cards?: ChatCard[];
}

const QUICK_CHIPS = [
  { label: "🟢 Live matches", message: "ekhon live match ki ki ache?" },
  { label: "🕐 Upcoming", message: "upcoming match gulo dekhao" },
  { label: "📊 Positions", message: "আমার positions দেখাও" },
  { label: "📋 Rules", message: "sob rules dekhao" },
  { label: "⚙️ Status", message: "/status" },
];

// ── Card renderer ─────────────────────────────────────────────
function CardView({
  card,
  onConfirmRule,
  onCancelRule,
  onApproveProposal,
  onRejectProposal,
  onAction,
}: {
  card: ChatCard;
  onConfirmRule: (id: string) => void;
  onCancelRule: (id: string) => void;
  onApproveProposal: (id: string) => void;
  onRejectProposal: (id: string) => void;
  onAction: (data: Record<string, any>) => void;
}) {
  if (card.type === "fixture_list") {
    const fixtures: any[] = (card.data as any).fixtures ?? [];
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>FIXTURES</Text>
        {fixtures.slice(0, 6).map((fx: any, i: number) => (
          <View key={i} style={styles.fixtureRow}>
            <Text style={[styles.badge, fx.state === "live" ? styles.badgeLive : styles.badgeUpcoming]}>
              {(fx.state ?? "?").toUpperCase()}
            </Text>
            <Text style={styles.fixtureText}>
              {fx.home_team ?? "?"} vs {fx.away_team ?? "?"}
              {fx.home_score != null ? `  ${fx.home_score}–${fx.away_score}` : ""}
            </Text>
          </View>
        ))}
      </View>
    );
  }

  if (card.type === "rule") {
    const d = card.data as any;
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>EXIT RULE</Text>
        <Text style={styles.cardBody}>
          {d.kind} — {d.trigger_type} @ {d.trigger_value} (size: {d.size ?? "all"})
        </Text>
        <Text style={styles.cardMeta}>Status: {d.status}</Text>
        {d.status === "PENDING" && (
          <View style={styles.cardActions}>
            <TouchableOpacity style={[styles.btn, styles.btnOk]} onPress={() => onConfirmRule(d.id)}>
              <Text style={styles.btnText}>✓ Confirm</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.btn, styles.btnBad]} onPress={() => onCancelRule(d.id)}>
              <Text style={styles.btnText}>✗ Cancel</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    );
  }

  if (card.type === "proposal") {
    const d = card.data as any;
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>TRADE PROPOSAL</Text>
        <Text style={styles.cardBody}>
          {d.side} @ {d.price_cents}¢ · ${d.amount_usd} · {d.edge_pp}pp edge
        </Text>
        {d.status === "PROPOSED" && (
          <View style={styles.cardActions}>
            <TouchableOpacity style={[styles.btn, styles.btnOk]} onPress={() => onApproveProposal(d.id)}>
              <Text style={styles.btnText}>✓ Approve</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.btn, styles.btnBad]} onPress={() => onRejectProposal(d.id)}>
              <Text style={styles.btnText}>✗ Reject</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    );
  }

  if (card.type === "action") {
    const d = card.data as any;
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>ACTION REQUIRED</Text>
        <Text style={styles.cardBody}>{d.label ?? "Confirm this action?"}</Text>
        <View style={styles.cardActions}>
          <TouchableOpacity style={[styles.btn, styles.btnBad]} onPress={() => onAction(d)}>
            <Text style={styles.btnText}>Confirm</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return null;
}

// ── ChatScreen ────────────────────────────────────────────────
export default function ChatScreen() {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const navigation = useNavigation<any>();

  // Welcome message
  useEffect(() => {
    setMessages([{
      id: "welcome",
      role: "assistant",
      content: "Assalamu Alaikum! 🏟️ PRED Agent here.\nআমি বাংলা, Banglish বা English এ কথা বলতে পারি।\n\nLive match, positions, rules, অথবা trade/rule তৈরি করতে জিজ্ঞেস করুন।",
    }]);
  }, []);

  const scrollToBottom = useCallback(() => {
    setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 100);
  }, []);

  useEffect(() => { scrollToBottom(); }, [messages, scrollToBottom]);

  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    const userMsg: LocalMessage = { id: Date.now().toString(), role: "user", content: trimmed };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const r = await api.chat(trimmed, sessionId);
      if (r.data) {
        if (!sessionId) setSessionId(r.data.session_id);
        const assistantMsg: LocalMessage = {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content: r.data.reply,
          cards: r.data.cards,
        };
        setMessages((prev) => [...prev, assistantMsg]);
      } else if (r.error?.code === "llm_not_configured") {
        setError("LLM কনফিগার করা নেই। সার্ভারে LLM_API_KEY এবং LLM_MODEL সেট করুন।");
      } else if (r.error?.code === "rate_limited") {
        setError("বেশি বার্তা পাঠাচ্ছেন। একটু অপেক্ষা করুন।");
      } else {
        setError(r.error?.message ?? "Chat error. Try again.");
      }
    } catch (_e) {
      setError("Network error. Check connection.");
    } finally {
      setLoading(false);
    }
  }, [loading, sessionId]);

  const clearChat = useCallback(async () => {
    Alert.alert("Clear Chat", "Clear conversation history?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Clear",
        style: "destructive",
        onPress: async () => {
          if (sessionId) await api.clearChat(sessionId);
          setSessionId(null);
          setMessages([{
            id: "cleared",
            role: "assistant",
            content: "Chat cleared. How can I help?",
          }]);
        },
      },
    ]);
  }, [sessionId]);

  const handleConfirmRule = useCallback(async (ruleId: string) => {
    const r = await api.confirmRule(ruleId, "biometric-confirmed");
    Alert.alert(r.ok ? "✓ Rule Confirmed" : "Error", r.ok ? "Exit rule is now ACTIVE." : (r.error?.message ?? "Failed"));
  }, []);

  const handleCancelRule = useCallback(async (ruleId: string) => {
    const r = await api.cancelRule(ruleId);
    Alert.alert(r.ok ? "Cancelled" : "Error", r.ok ? "Rule cancelled." : (r.error?.message ?? "Failed"));
  }, []);

  const handleApproveProposal = useCallback(async (proposalId: string) => {
    const r = await api.approveProposal(proposalId, {}, "biometric-confirmed");
    Alert.alert(r.ok ? "✓ Approved" : "Error", r.ok ? "Proposal approved." : (r.error?.message ?? "Failed"));
  }, []);

  const handleRejectProposal = useCallback(async (proposalId: string) => {
    const r = await api.rejectProposal(proposalId);
    Alert.alert(r.ok ? "Rejected" : "Error", r.ok ? "Proposal rejected." : (r.error?.message ?? "Failed"));
  }, []);

  const handleAction = useCallback(async (d: Record<string, any>) => {
    const action = d.action as string;
    if (action === "pause") {
      const r = await api.pause("biometric-confirmed");
      Alert.alert(r.ok ? "Paused" : "Error", r.ok ? "Agent paused." : (r.error?.message ?? "Failed"));
    } else if (action === "panic") {
      Alert.alert("PANIC", "Pause, reject proposals, cancel orders AND all rules?", [
        { text: "Cancel", style: "cancel" },
        {
          text: "PANIC",
          style: "destructive",
          onPress: async () => {
            const r = await api.panic("biometric-confirmed");
            Alert.alert(r.ok ? "Panic done" : "Error", r.ok ? "Agent paused, everything cancelled." : (r.error?.message ?? "Failed"));
          },
        },
      ]);
    } else if (action === "cancel_order") {
      const r = await api.cancelOrder(d.order_id, "biometric-confirmed");
      Alert.alert(r.ok ? "Cancelled" : "Error", r.ok ? "Order cancelled." : (r.error?.message ?? "Failed"));
    } else if (action === "cancel_rule") {
      const r = await api.cancelRule(d.rule_id);
      Alert.alert(r.ok ? "Cancelled" : "Error", r.ok ? "Rule cancelled." : (r.error?.message ?? "Failed"));
    }
  }, []);

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={Platform.OS === "ios" ? 88 : 0}
    >
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => navigation.goBack()}>
          <Text style={styles.backText}>← Back</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Chat</Text>
        <View style={styles.backBtn} />
      </View>

      {/* Messages list */}
      <ScrollView
        ref={scrollRef}
        style={styles.messages}
        contentContainerStyle={styles.messagesContent}
        keyboardDismissMode="on-drag"
      >
        {messages.map((msg) => (
          <View
            key={msg.id}
            style={[styles.msgBubble, msg.role === "user" ? styles.userBubble : styles.aiBubble]}
          >
            <Text style={styles.roleLabel}>{msg.role === "user" ? "You" : "Agent"}</Text>
            {/* textContent equivalent: Text component never executes scripts */}
            <Text style={styles.msgText}>{msg.content}</Text>
            {msg.cards?.map((card, i) => (
              <CardView
                key={i}
                card={card}
                onConfirmRule={handleConfirmRule}
                onCancelRule={handleCancelRule}
                onApproveProposal={handleApproveProposal}
                onRejectProposal={handleRejectProposal}
                onAction={handleAction}
              />
            ))}
          </View>
        ))}
        {loading && (
          <View style={[styles.msgBubble, styles.aiBubble]}>
            <ActivityIndicator size="small" color="#4c8dff" />
            <Text style={styles.loadingText}>Thinking…</Text>
          </View>
        )}
        {error && (
          <View style={styles.errorBanner}>
            <Text style={styles.errorText}>⚠️ {error}</Text>
          </View>
        )}
      </ScrollView>

      {/* Quick chips */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chips} contentContainerStyle={styles.chipsContent}>
        {QUICK_CHIPS.map((chip) => (
          <TouchableOpacity key={chip.label} style={styles.chip} onPress={() => sendMessage(chip.message)}>
            <Text style={styles.chipText}>{chip.label}</Text>
          </TouchableOpacity>
        ))}
        <TouchableOpacity style={[styles.chip, styles.chipDanger]} onPress={clearChat}>
          <Text style={styles.chipText}>🗑 Clear</Text>
        </TouchableOpacity>
      </ScrollView>

      {/* Input row */}
      <View style={styles.inputRow}>
        <TextInput
          style={styles.textInput}
          value={input}
          onChangeText={setInput}
          placeholder="Chat in Bengali, Banglish or English…"
          placeholderTextColor="#8a90a0"
          multiline
          maxLength={4000}
          returnKeyType="send"
          onSubmitEditing={() => { if (!loading) sendMessage(input); }}
          blurOnSubmit
        />
        <TouchableOpacity
          style={[styles.sendBtn, (!input.trim() || loading) && styles.sendBtnDisabled]}
          onPress={() => sendMessage(input)}
          disabled={!input.trim() || loading}
        >
          <Text style={styles.sendText}>Send</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

// ── Styles ────────────────────────────────────────────────────
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0f1115" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#272b35",
  },
  backBtn: { paddingHorizontal: 4, paddingVertical: 4, minWidth: 56 },
  backText: { color: "#4c8dff", fontSize: 14, fontWeight: "600" },
  headerTitle: { color: "#e8eaf0", fontSize: 16, fontWeight: "700" },
  messages: { flex: 1 },
  messagesContent: { padding: 12, gap: 10, paddingBottom: 8 },
  msgBubble: { borderRadius: 12, padding: 12, maxWidth: "85%" },
  userBubble: { backgroundColor: "#1e2a3a", alignSelf: "flex-end", borderRadius: 12 },
  aiBubble: { backgroundColor: "#1a1e26", alignSelf: "flex-start", borderColor: "#272b35", borderWidth: 1 },
  roleLabel: { fontSize: 10, color: "#8a90a0", marginBottom: 4 },
  msgText: { color: "#e8eaf0", fontSize: 14, lineHeight: 20 },
  loadingText: { color: "#8a90a0", fontSize: 13, marginTop: 4 },
  errorBanner: { backgroundColor: "#2a1518", borderRadius: 8, padding: 10, marginHorizontal: 4 },
  errorText: { color: "#e5484d", fontSize: 13 },

  chips: { maxHeight: 48, borderTopWidth: 1, borderTopColor: "#272b35" },
  chipsContent: { paddingHorizontal: 12, paddingVertical: 8, gap: 8 },
  chip: { backgroundColor: "#1a1e26", borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6, borderWidth: 1, borderColor: "#272b35" },
  chipDanger: { borderColor: "#e5484d44" },
  chipText: { color: "#e8eaf0", fontSize: 12 },

  inputRow: { flexDirection: "row", padding: 12, gap: 8, borderTopWidth: 1, borderTopColor: "#272b35", alignItems: "flex-end" },
  textInput: { flex: 1, backgroundColor: "#181b22", color: "#e8eaf0", borderRadius: 10, padding: 10, fontSize: 14, maxHeight: 100, borderWidth: 1, borderColor: "#272b35" },
  sendBtn: { backgroundColor: "#4c8dff", borderRadius: 10, paddingHorizontal: 16, paddingVertical: 10, justifyContent: "center" },
  sendBtnDisabled: { opacity: 0.4 },
  sendText: { color: "#fff", fontSize: 14, fontWeight: "600" },

  // Cards
  card: { marginTop: 8, borderRadius: 8, padding: 10, backgroundColor: "#0f1115", borderWidth: 1, borderColor: "#272b35" },
  cardTitle: { fontSize: 10, color: "#8a90a0", fontWeight: "700", textTransform: "uppercase", marginBottom: 6 },
  cardBody: { color: "#e8eaf0", fontSize: 13, marginBottom: 4 },
  cardMeta: { color: "#8a90a0", fontSize: 12 },
  cardActions: { flexDirection: "row", gap: 8, marginTop: 8 },
  btn: { borderRadius: 8, paddingHorizontal: 14, paddingVertical: 7 },
  btnOk: { backgroundColor: "#2fbf71" },
  btnBad: { backgroundColor: "#e5484d" },
  btnText: { color: "#fff", fontSize: 13, fontWeight: "600" },

  fixtureRow: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 3 },
  badge: { fontSize: 10, fontWeight: "700", paddingHorizontal: 5, paddingVertical: 2, borderRadius: 4 },
  badgeLive: { backgroundColor: "#e5484d22", color: "#e5484d" },
  badgeUpcoming: { backgroundColor: "#4c8dff22", color: "#4c8dff" },
  fixtureText: { color: "#e8eaf0", fontSize: 13, flex: 1 },
});
