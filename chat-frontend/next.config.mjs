/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    serverActions: { allowedOrigins: ["localhost:3000"] },
  },
  async rewrites() {
    // Proxy do backend FastAPI durante o dev. Em prod (Vercel/Railway) usar
    // env var PUBLIC_API_URL direto no fetch.
    return [
      {
        source: "/api/backend/:path*",
        destination: (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
          + "/:path*",
      },
    ];
  },
};
export default nextConfig;
