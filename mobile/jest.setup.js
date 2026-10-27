// jest.setup.js
// Mock expo-secure-store for tests

jest.mock("expo-secure-store", () => {
  const store = {};
  return {
    getItemAsync: jest.fn(async (key) => store[key] || null),
    setItemAsync: jest.fn(async (key, value) => {
      store[key] = value;
    }),
    deleteItemAsync: jest.fn(async (key) => {
      delete store[key];
    }),
  };
});

// Mock expo-local-authentication
jest.mock("expo-local-authentication", () => ({
  authenticateAsync: jest.fn(async () => ({ success: true })),
  hasHardwareAsync: jest.fn(async () => true),
  isEnrolledAsync: jest.fn(async () => true),
}));
