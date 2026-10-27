// Zustand stores

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import * as SecureStore from "expo-secure-store";
import { Mode, StatusResponse, Proposal, Order, Position, Device } from "../types/api";

// SecureStore driver for zustand persist
const secureStorage = {
  getItem: async (name: string): Promise<string | null> => {
    try {
      return await SecureStore.getItemAsync(name);
    } catch {
      return null;
    }
  },
  setItem: async (name: string, value: string) => {
    try {
      await SecureStore.setItemAsync(name, value);
    } catch {
      // ignore
    }
  },
  removeItem: async (name: string) => {
    try {
      await SecureStore.deleteItemAsync(name);
    } catch {
      // ignore
    }
  },
};

export interface AuthStore {
  deviceToken: string | null;
  isAuthenticated: boolean;
  setToken: (token: string | null) => void;
  clearToken: () => void;
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set) => ({
      deviceToken: null,
      isAuthenticated: false,
      setToken: (token) => set({ deviceToken: token, isAuthenticated: !!token }),
      clearToken: () => set({ deviceToken: null, isAuthenticated: false }),
    }),
    {
      name: "auth-storage",
      storage: createJSONStorage(() => secureStorage),
    }
  )
);

export interface UIStore {
  mode: Mode | null;
  paused: boolean;
  feedsOk: boolean;
  balance: string;
  exposure: string;
  dailyPnl: string;
  pendingProposals: number;
  setStatus: (status: StatusResponse) => void;
  setPaused: (paused: boolean) => void;
}

export const useUIStore = create<UIStore>((set) => ({
  mode: null,
  paused: false,
  feedsOk: false,
  balance: "0.00",
  exposure: "0.00",
  dailyPnl: "0.00",
  pendingProposals: 0,
  setStatus: (status) =>
    set({
      mode: status.mode,
      paused: status.paused,
      feedsOk: status.feeds_ok,
      balance: status.balance,
      exposure: status.exposure,
      dailyPnl: status.daily_pnl,
    }),
  setPaused: (paused) => set({ paused }),
}));

export interface ProposalsStore {
  proposals: Proposal[];
  setProposals: (proposals: Proposal[]) => void;
  addProposal: (proposal: Proposal) => void;
  updateProposal: (id: string, patch: Partial<Proposal>) => void;
  removeProposal: (id: string) => void;
}

export const useProposalsStore = create<ProposalsStore>((set) => ({
  proposals: [],
  setProposals: (proposals) => set({ proposals }),
  addProposal: (proposal) =>
    set((state) => ({ proposals: [...state.proposals, proposal] })),
  updateProposal: (id, patch) =>
    set((state) => ({
      proposals: state.proposals.map((p) =>
        p.id === id ? { ...p, ...patch } : p
      ),
    })),
  removeProposal: (id) =>
    set((state) => ({ proposals: state.proposals.filter((p) => p.id !== id) })),
}));

export interface OrdersStore {
  orders: Order[];
  setOrders: (orders: Order[]) => void;
}

export const useOrdersStore = create<OrdersStore>((set) => ({
  orders: [],
  setOrders: (orders) => set({ orders }),
}));

export interface PositionsStore {
  positions: Position[];
  setPositions: (positions: Position[]) => void;
}

export const usePositionsStore = create<PositionsStore>((set) => ({
  positions: [],
  setPositions: (positions) => set({ positions }),
}));

export interface DevicesStore {
  devices: Device[];
  setDevices: (devices: Device[]) => void;
}

export const useDevicesStore = create<DevicesStore>((set) => ({
  devices: [],
  setDevices: (devices) => set({ devices }),
}));
