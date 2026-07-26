import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export: every page is a client component fetching at runtime, so
  // there's nothing to server-render. A static bundle deploys on Render's
  // free static tier — CDN-served, no cold starts (free web services sleep).
  output: "export",
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "media.api-sports.io" },
      { protocol: "https", hostname: "a.espncdn.com" },
      { protocol: "https", hostname: "bcciplayerimages.s3.ap-south-1.amazonaws.com" },
    ],
  },
};

export default nextConfig;
