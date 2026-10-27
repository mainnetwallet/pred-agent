// Probability gauge — signature component (kit/08_MOBILE_APP.md visual direction)
// Market price vs my p_fair range as two markers on one bar

import React from "react";
import { View, Text, StyleSheet } from "react-native";

interface ProbabilityGaugeProps {
  marketPrice: number | null; // 0-100 (cents)
  pFair: number | null; // 0-1
  pLow: number | null;
  pHigh: number | null;
  side: "long" | "short" | "neutral";
}

export const ProbabilityGauge: React.FC<ProbabilityGaugeProps> = ({
  marketPrice,
  pFair,
  pLow,
  pHigh,
  side,
}) => {
  const barWidth = 200;
  const barHeight = 8;

  const getPosition = (value: number | null) => {
    if (value === null) return 0;
    // value is 0-1 for p_fair, 0-100 for market price
    const normalized = value > 1 ? value / 100 : value;
    return Math.max(0, Math.min(100, normalized * 100));
  };

  const marketPos = getPosition(marketPrice);
  const fairPos = getPosition(pFair);
  const lowPos = getPosition(pLow);
  const highPos = getPosition(pHigh);

  const sideColor =
    side === "long" ? "#22C55E" : side === "short" ? "#EF4444" : "#60A5FA";

  return (
    <View style={styles.container}>
      <View style={styles.barTrack}>
        {/* Range band (low to high) */}
        {pLow !== null && pHigh !== null && (
          <View
            style={[
              styles.barBand,
              {
                left: `${lowPos}%`,
                width: `${Math.max(0, highPos - lowPos)}%`,
                backgroundColor: sideColor + "40", // 25% opacity
              },
            ]}
          />
        )}
        {/* Market price tick */}
        {marketPrice !== null && (
          <View
            style={[
              styles.barTick,
              { left: `${marketPos}%`, backgroundColor: "#666" },
            ]}
          />
        )}
        {/* p_fair dot */}
        {pFair !== null && (
          <View
            style={[
              styles.barDot,
              { left: `${fairPos}%`, backgroundColor: sideColor },
            ]}
          />
        )}
      </View>
      <View style={styles.labels}>
        <Text style={styles.label}>
          Market: {marketPrice !== null ? formatPriceCents(String(marketPrice)) : "—"}
        </Text>
        <Text style={styles.label}>
          My p_fair:{pFair !== null ? ` ${(pFair * 100).toFixed(0)}%` : " —"}
        </Text>
      </View>
    </View>
  );
};

function formatPriceCents(priceCents: string): string {
  const num = parseFloat(priceCents);
  if (isNaN(num)) return "0.00";
  return `$${(num / 100).toFixed(2)}`;
}

const styles = StyleSheet.create({
  container: {
    marginVertical: 8,
  },
  barTrack: {
    height: 8,
    backgroundColor: "#E0E0E0",
    borderRadius: 4,
    overflow: "hidden",
    width: 200,
    position: "relative",
  },
  barBand: {
    position: "absolute",
    top: 0,
    bottom: 0,
    borderRadius: 4,
  },
  barTick: {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: 2,
    backgroundColor: "#666",
  },
  barDot: {
    position: "absolute",
    top: -4,
    bottom: -4,
    width: 12,
    height: 12,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: "#fff",
  },
  labels: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 4,
  },
  label: {
    fontSize: 11,
    color: "#666",
    fontFamily: "monospace",
  },
});
