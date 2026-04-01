"use client";

import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import Sidebar from "./sidebar";
import { SidebarContext } from "./sidebar-context";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [mounted, setMounted] = useState(false);
  const isLoginPage = pathname === "/";

  useEffect(() => { setMounted(true); }, []);

  if (isLoginPage) {
    return <>{children}</>;
  }

  // Avoid hydration mismatch: render without sidebar until client mount
  if (!mounted) {
    return <div className="min-h-screen">{children}</div>;
  }

  return (
    <SidebarContext.Provider value={{ collapsed, setCollapsed }}>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className={`flex-1 min-h-screen transition-all duration-200 ${collapsed ? "ml-14" : "ml-48"}`}>
          {children}
        </main>
      </div>
    </SidebarContext.Provider>
  );
}
