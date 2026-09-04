import type { Metadata } from "next";
import { Cinzel, Spectral } from "next/font/google";

import "./globals.css";

const spectral = Spectral({
  variable: "--font-spectral",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const cinzel = Cinzel({
  variable: "--font-cinzel",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
});

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "Dungeon Share",
    template: "%s · Dungeon Share",
  },
  description:
    "Campaign stories, artifacts, and faces worth remembering.",
  openGraph: {
    title: "Dungeon Share",
    description:
      "Campaign stories, artifacts, and faces worth remembering.",
    type: "website",
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: "Dungeon Share campaign journal",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Dungeon Share",
    description:
      "Campaign stories, artifacts, and faces worth remembering.",
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${spectral.variable} ${cinzel.variable}`}>
      <body>{children}</body>
    </html>
  );
}
