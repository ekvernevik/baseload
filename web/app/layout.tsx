import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";

// Geist fonts ship locally via the `geist` package — no build-time fetch to
// Google Fonts. (Using next/font/google here caused the dev compile to hang on
// stalled font downloads from fonts.gstatic.com.)
const geistSans = GeistSans;
const geistMono = GeistMono;

export const metadata: Metadata = {
  title: "Baseload — Nordic Grid Intelligence",
  description: "Decision intelligence platform for Nordic electricity markets. We model when and why the grid gets stressed, and what a battery storage asset is worth under those conditions.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
