import type { NextConfig } from "next";

function webRevision(): string {
  return (
    process.env.OPENACTS_WEB_REVISION ??
    process.env.VERCEL_GIT_COMMIT_SHA ??
    "development"
  );
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [{ key: "OpenActs-Web-Revision", value: webRevision() }],
      },
    ];
  },
};

export default nextConfig;
