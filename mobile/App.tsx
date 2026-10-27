// Main app entry — Expo Router not used; simple navigator with screens

import "react-native-gesture-handler";
import { useEffect, useState } from "react";
import {
  View,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { createStackNavigator } from "@react-navigation/stack";
import { PairingScreen } from "./screens/PairingScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { ProposalsScreen } from "./screens/ProposalsScreen";
import { PanicScreen } from "./screens/PanicScreen";
import { LimitsSettingsScreen } from "./screens/LimitsSettingsScreen";
import { useAuthStore } from "./store/index";
import { wsClient } from "./services/websocket";
import { api } from "./services/api";

const Stack = createStackNavigator();

export default function App() {
  const { deviceToken, isAuthenticated } = useAuthStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check for existing token
    // In production, this would read from SecureStore via zustand persist
    setLoading(false);
  }, []);

  useEffect(() => {
    if (deviceToken) {
      api.setToken(deviceToken);
      wsClient.setToken(deviceToken);
    }
  }, [deviceToken]);

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator size="large" />
      </View>
    );
  }

  return (
    <NavigationContainer>
      <Stack.Navigator screenOptions={{ headerShown: false }}>
        {!isAuthenticated ? (
          <Stack.Screen name="Pairing" component={PairingScreen} />
        ) : (
          <>
            <Stack.Screen name="Home" component={HomeScreen} />
            <Stack.Screen name="Proposals" component={ProposalsScreen} />
            <Stack.Screen name="Panic" component={PanicScreen} />
            <Stack.Screen name="LimitsSettings" component={LimitsSettingsScreen} />
          </>
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
});
