import type { NextConfig } from "next";

/**
 * No `rewrites()` here on purpose. Next resolves rewrites at build time, which bakes AGENT_URL into
 * the image — fatal for a container that is built once and pointed at different agents. The proxy
 * lives in `src/app/api/[...path]/route.ts` and reads the variable per request instead.
 */
const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
