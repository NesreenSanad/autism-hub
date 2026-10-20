import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tawasol · تواصل",
  description: "Autism services in Egypt · دليل خدمات التوحد في مصر",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ar" dir="rtl">
      <body className="antialiased min-h-screen">{children}</body>
    </html>
  );
}
