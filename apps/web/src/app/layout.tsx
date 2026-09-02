import type { Metadata } from "next";
import { Barlow_Condensed, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";
import { TopBar } from "@/components/TopBar";

const barlow = Barlow_Condensed({ variable: "--font-barlow", subsets: ["latin"], weight: ["500", "600", "700"] });
const plexSans = IBM_Plex_Sans({ variable: "--font-plex-sans", subsets: ["latin"], weight: ["400", "500", "600"] });
const plexMono = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400", "500"] });

const TITLE = "ScenePilot — production control room";
const DESCRIPTION =
  "Production intelligence and shoot rescue: external evidence, structured production state, constraint reasoning, human-approved recovery.";

/**
 * Cards only unfurl against an absolute origin, and this image is deployed by `deploy/cloudrun.sh`
 * to whatever hostname Cloud Run hands out — so read the origin off the request rather than baking
 * a guess in at build time. `NEXT_PUBLIC_SITE_URL` wins when a stable domain does exist.
 */
async function origin(): Promise<URL> {
  const configured = process.env.NEXT_PUBLIC_SITE_URL;
  if (configured) return new URL(configured);
  const h = await headers();
  const host = h.get("x-forwarded-host") || h.get("host");
  if (!host) return new URL("http://localhost:3000");
  const proto = h.get("x-forwarded-proto") || (host.startsWith("localhost") ? "http" : "https");
  return new URL(`${proto}://${host}`);
}

export async function generateMetadata(): Promise<Metadata> {
  return {
    metadataBase: await origin(),
    title: TITLE,
    description: DESCRIPTION,
    applicationName: "ScenePilot",
    openGraph: {
      type: "website",
      siteName: "ScenePilot",
      title: TITLE,
      description: DESCRIPTION,
      url: "/",
    },
    twitter: {
      card: "summary_large_image",
      title: TITLE,
      description: DESCRIPTION,
    },
  };
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${barlow.variable} ${plexSans.variable} ${plexMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <TopBar />
        <main className="flex-1 w-full max-w-[1480px] mx-auto px-5 py-6">{children}</main>
        <footer className="px-5 py-4 text-[11px] text-dim border-t border-line">
          Project Nightfall is an original fictional production. All cast, locations, permits and prices are synthetic. Web evidence is live and real.
        </footer>
      </body>
    </html>
  );
}
