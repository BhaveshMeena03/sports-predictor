import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import Sidebar from "./components/Sidebar";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const SITE = "https://sports-predictor-7nf6.onrender.com";
const TITLE = "Sports Predictor — predictions scored in public";

// This string is the first thing a reader sees in a link preview or a search
// result. The old copy said "AI-powered sports betting analysis" — the exact
// claim every page on this site spends its footer disclaiming, and the wrong
// first impression for anyone evaluating the project.
const DESCRIPTION =
  "A football prediction model that logs every forecast before kickoff and " +
  "scores it in public — misses included. Machine-payable per call over " +
  "x402 on Base. Informational only, not betting advice.";

export const metadata: Metadata = {
  // Scrapers reject relative URLs, and static export has no request context to
  // infer an origin from, so the absolute base has to be stated.
  metadataBase: new URL(SITE),
  title: TITLE,
  description: DESCRIPTION,
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    url: SITE,
    siteName: "Sports Predictor",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-screen flex" style={{ background: "var(--bg-primary)" }}>
        <Sidebar />
        <main className="flex-1 ml-64 p-6 overflow-y-auto">
          {children}
        </main>
      </body>
    </html>
  );
}
