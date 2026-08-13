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

const SITE = "https://predictor.lexthedev.com";
const TITLE = "Sports Predictor — predictions scored in public";
const IMAGE_ALT =
  "Sports Predictor — 104 matches scored in public, 64.4% accuracy";

// This string is the first thing a reader sees in a link preview or a search
// result. The old copy said "AI-powered sports betting analysis" — the exact
// claim every page on this site spends its footer disclaiming, and the wrong
// first impression for anyone evaluating the project.
const DESCRIPTION =
  "A football prediction model that scores every forecast in public — misses " +
  "included — and states how each one was produced. Machine-payable per call " +
  "over x402 on Base. Informational only, not betting advice.";

// The card is served from /og.png — a plain file in public/, referenced
// explicitly rather than through Next's app/opengraph-image convention.
//
// Two problems drove this. The generated route wrote to an extensionless
// path, which Render served as binary/octet-stream, and scrapers reject an
// og:image that does not arrive with an image content type. The static
// convention fixed the MIME type but still appended a build hash as a query
// string, and a bare path is the shape every scraper handles without
// question. public/ is copied verbatim, so the URL is stable across builds
// and cannot pick up a hash again.
//
// The JSX that generated this image is in git history at commit 8988482.
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
    images: [{ url: "/og.png", width: 1200, height: 630, alt: IMAGE_ALT }],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    images: [{ url: "/og.png", width: 1200, height: 630, alt: IMAGE_ALT }],
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
