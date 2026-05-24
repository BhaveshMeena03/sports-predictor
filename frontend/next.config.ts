import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "media.api-sports.io" },
      { protocol: "https", hostname: "a.espncdn.com" },
      { protocol: "https", hostname: "bcciplayerimages.s3.ap-south-1.amazonaws.com" },
    ],
  },
};

export default nextConfig;
