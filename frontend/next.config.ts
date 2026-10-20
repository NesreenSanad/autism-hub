import type { NextConfig } from "next";

// Static export: `npm run build` writes plain files to `out/`, which any web
// host can serve (cPanel public folder, Vercel, Netlify, GitHub Pages).
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
