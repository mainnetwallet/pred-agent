// Guards the app icon wiring: every asset referenced from app.json must exist with the size
// the stores / Android require, otherwise `expo prebuild` (and the APK build) fails late.

import * as fs from "fs";
import * as path from "path";

const root = path.resolve(__dirname, "..");
const config = JSON.parse(fs.readFileSync(path.join(root, "app.json"), "utf8")).expo;

function pngInfo(rel: string): { width: number; height: number; hasAlpha: boolean } {
  const buf = fs.readFileSync(path.join(root, rel));
  expect(buf.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a"); // PNG signature
  return {
    width: buf.readUInt32BE(16),
    height: buf.readUInt32BE(20),
    hasAlpha: buf[25] === 6 || buf[25] === 4, // colour type RGBA / grey+alpha
  };
}

describe("app icon wiring", () => {
  it("has a 1024x1024 opaque app icon (iOS rejects alpha)", () => {
    expect(config.icon).toBe("./assets/icon.png");
    expect(pngInfo(config.icon)).toEqual({ width: 1024, height: 1024, hasAlpha: false });
  });

  it("has a 1024x1024 transparent Android adaptive foreground on the brand background", () => {
    const a = config.android.adaptiveIcon;
    expect(a.backgroundColor).toBe("#0B0F14");
    expect(pngInfo(a.foregroundImage)).toEqual({ width: 1024, height: 1024, hasAlpha: true });
  });

  it("has a transparent splash image on the brand background", () => {
    expect(config.splash.backgroundColor).toBe("#0B0F14");
    expect(pngInfo(config.splash.image).hasAlpha).toBe(true);
  });

  it("has a 96x96 notification icon for expo-notifications", () => {
    const entry = config.plugins.find((p: unknown) => Array.isArray(p) && p[0] === "expo-notifications");
    expect(entry).toBeDefined();
    const { icon, color } = entry[1];
    expect(color).toBe("#60A5FA");
    expect(pngInfo(icon)).toEqual({ width: 96, height: 96, hasAlpha: true });
  });

  it("has a web favicon", () => {
    expect(pngInfo(config.web.favicon).width).toBe(48);
  });
});
