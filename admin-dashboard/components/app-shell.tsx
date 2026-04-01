"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import Sidebar from "./sidebar";
import { SidebarContext } from "./sidebar-context";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const isLoginPage = pathname === "/";

  if (isLoginPage) {
    return <>{children}</>;
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
