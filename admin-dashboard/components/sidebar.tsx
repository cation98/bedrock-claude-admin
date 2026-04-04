"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { logout, getUser } from "@/lib/auth";
import { useSidebar } from "./sidebar-context";

const NAV_ITEMS = [
  { href: "/dashboard", label: "운용현황", icon: "📊" },
  { href: "/users", label: "사용자 관리", icon: "👤" },
  { href: "/apps", label: "앱 관리", icon: "📱" },
  { href: "/audit", label: "감사 로그", icon: "📋" },
  { href: "/security", label: "보안 정책", icon: "🛡" },
  { href: "/quota", label: "토큰 정책", icon: "💰" },
  { href: "/usage", label: "토큰 사용량", icon: "📈" },
  { href: "/infra", label: "인프라", icon: "⚙" },
  { href: "/network", label: "네트워크", icon: "🌐" },
  { href: "/surveys", label: "현장 수집", icon: "📝" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const user = getUser();
  const { collapsed, setCollapsed } = useSidebar();

  // Don't render sidebar on login page
  if (pathname === "/") return null;

  return (
    <aside
      className={`fixed left-0 top-0 z-30 flex h-screen flex-col border-r border-gray-200 bg-white transition-all duration-200 ${
        collapsed ? "w-14" : "w-48"
      }`}
    >
      {/* Logo / Title */}
      <div className="flex items-center justify-between border-b border-gray-200 px-3 py-3">
        {!collapsed && (
          <span className="text-sm font-bold text-gray-900 truncate">Claude Admin</span>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          title={collapsed ? "펼치기" : "접기"}
        >
          {collapsed ? "▶" : "◀"}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-2">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-2.5 px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "bg-blue-50 text-blue-700 font-medium border-r-2 border-blue-600"
                  : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
              }`}
              title={collapsed ? item.label : undefined}
            >
              <span className="text-base flex-shrink-0">{item.icon}</span>
              {!collapsed && <span className="truncate">{item.label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* User / Logout */}
      <div className="border-t border-gray-200 px-3 py-3">
        {!collapsed && user && (
          <div className="mb-2 truncate text-xs text-gray-500">{user.name}</div>
        )}
        <button
          onClick={logout}
          className={`flex items-center gap-2 rounded-md text-sm text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors ${
            collapsed ? "justify-center p-1.5" : "px-2 py-1.5 w-full"
          }`}
          title="로그아웃"
        >
          <span className="text-base">🚪</span>
          {!collapsed && <span>로그아웃</span>}
        </button>
      </div>
    </aside>
  );
}
