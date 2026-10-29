// PRED Agent brand mark: a geometric "P" (stem + ring) with a green dot in the counter —
// the "fair probability" marker from the probability gauge. Drawn with react-native-svg so it
// stays crisp at any size. Geometry mirrors assets/branding/build_assets.py; colours come from
// DESIGN.md (not theme-dependent: the badge is always the dark navy tile).

import React from "react";
import Svg, { Circle, Path, Rect } from "react-native-svg";

export const BRAND = {
  bg: "#0B0F14",
  blue: "#60A5FA",
  green: "#22C55E",
  edge: "#2A3644",
} as const;

// The unscaled mark's bounding-box centre is (271, 261); scale 1.05 about the canvas centre.
const S = 1.05;
const fx = (x: number) => 256 + S * (x - 271);
const fy = (y: number) => 256 + S * (y - 261);

interface LogoProps {
  size?: number;
  /** Draw the rounded navy tile behind the mark (default). Pass false for a bare mark. */
  badge?: boolean;
}

export const Logo: React.FC<LogoProps> = ({ size = 64, badge = true }) => (
  <Svg
    width={size}
    height={size}
    viewBox="0 0 512 512"
    accessibilityRole="image"
    accessibilityLabel="PRED Agent logo"
  >
    {badge && (
      <>
        <Rect width={512} height={512} rx={112} fill={BRAND.bg} />
        <Rect x={4} y={4} width={504} height={504} rx={108} fill="none" stroke={BRAND.edge} strokeWidth={8} />
      </>
    )}
    <Path
      d={`M${fx(172)} ${fy(392)}V${fy(132)}`}
      fill="none"
      stroke={BRAND.blue}
      strokeWidth={44 * S}
      strokeLinecap="round"
    />
    <Circle cx={fx(286)} cy={fy(214)} r={84 * S} fill="none" stroke={BRAND.blue} strokeWidth={44 * S} />
    <Circle cx={fx(286)} cy={fy(214)} r={26 * S} fill={BRAND.green} />
  </Svg>
);
