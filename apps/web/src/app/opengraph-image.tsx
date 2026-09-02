import { ImageResponse } from "next/og";

export const alt = "ScenePilot — a production control room that plans scenes against live web evidence and rescues shoot days";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const STRIPS = [
  { color: "#f2d45c", width: 210 },
  { color: "#86b6ff", width: 96 },
  { color: "#f2a35e", width: 148 },
  { color: "#eef1f5", width: 74 },
  { color: "#74d29a", width: 122 },
  { color: "#f0716b", width: 186 },
  { color: "#262e3b", width: 240 },
];

const TAGS = ["Parallel Search · Task · FindAll", "Gemini + ADK", "Day 4 · Mumbai · rain 13:00–17:00"];

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#0f1218",
          padding: "68px 72px",
          color: "#e9edf3",
        }}
      >
        <div style={{ display: "flex", gap: 8 }}>
          {STRIPS.map((strip) => (
            <div key={strip.color + strip.width} style={{ width: strip.width, height: 12, borderRadius: 3, background: strip.color }} />
          ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", fontSize: 128, fontWeight: 700, letterSpacing: -2, lineHeight: 1 }}>
            <span>SCENE</span>
            <span style={{ color: "#f2b544" }}>PILOT</span>
          </div>
          <div style={{ display: "flex", marginTop: 26, fontSize: 40, color: "#a1abbf" }}>
            A production control room for the day the weather turns
          </div>
          <div style={{ display: "flex", marginTop: 14, fontSize: 27, color: "#6c7690", maxWidth: 940 }}>
            External evidence, structured production state, constraint reasoning, human-approved recovery.
          </div>
        </div>

        <div style={{ display: "flex", gap: 14 }}>
          {TAGS.map((tag) => (
            <div
              key={tag}
              style={{
                display: "flex",
                padding: "10px 20px",
                borderRadius: 8,
                border: "1px solid #364155",
                background: "#1a2029",
                fontSize: 24,
                color: "#a1abbf",
              }}
            >
              {tag}
            </div>
          ))}
        </div>
      </div>
    ),
    { ...size },
  );
}
