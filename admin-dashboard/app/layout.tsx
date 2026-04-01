import type { Metadata } from "next";
import "./globals.css";
import AppShell from "@/components/app-shell";

export const metadata: Metadata = {
  title: "Claude Code Admin",
  description: "Bedrock Claude Code 관리 대시보드",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="min-h-screen bg-gray-50 text-gray-900 antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
