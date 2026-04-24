"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { logout, getUser } from "@/lib/auth";
import { useSidebar } from "./sidebar-context";

const NAV_ITEMS = [
  { href: "/dashboard", label: "운용현황", icon: "📊" },
  { href: "/users", label: "사용자 관리", icon: "👤" },
  { href: "/apps", label: "앱 갤러리", icon: "📱" },
  { href: "/apps/manage", label: "앱 운영 관리", icon: "🖥" },
  { href: "/apps/pending", label: "배포 승인", icon: "✅" },
  { href: "/audit", label: "감사 로그", icon: "📋" },
  { href: "/security", label: "보안 정책", icon: "🛡" },
  { href: "/data-governance", label: "거버넌스", icon: "📁" },
  { href: "/skills-governance", label: "스킬 거버넌스", icon: "🎯" },
  { href: "/quota", label: "토큰 정책", icon: "💰" },
  { href: "/usage", label: "토큰 사용량", icon: "📈" },
  { href: "/infra", label: "인프라", icon: "⚙" },
  { href: "/network", label: "네트워크", icon: "🌐" },
  { href: "/surveys", label: "현장 수집", icon: "📝" },
  { href: "/broadcast", label: "공지 발송", icon: "📢" },
  { href: "/announcements", label: "공지 관리", icon: "📌" },
  { href: "/maintenance", label: "점검 모드", icon: "🔧" },
  { href: "/workflows", label: "워크플로우", icon: "⚙️" },
  { href: "/analytics/ui-split", label: "UI 분석", icon: "🔀" },
  { href: "/analytics/knowledge-graph", label: "지식 그래프", icon: "🧠" },
  { href: "/analytics/knowledge-trends", label: "지식 추이", icon: "📡" },
  { href: "/analytics/knowledge-gap", label: "갭 분석", icon: "🔍" },
  { href: "/analytics/departments", label: "부서 분포", icon: "🏢" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const user = getUser();
  const { collapsed, setCollapsed } = useSidebar();

  // Don't render sidebar on login page
  if (pathname === "/") return null;

  return (
    <aside
      className={`fixed left-0 top-0 z-30 flex h-screen flex-col border-r border-[var(--border)] bg-[var(--surface)] transition-all duration-200 ${
        collapsed ? "w-14" : "w-48"
      }`}
    >
      {/* Logo / Title */}
      <div className="flex items-center justify-between border-b border-[var(--border)] px-3 py-3">
        {!collapsed && (
          <span className="text-sm font-bold text-[var(--text-primary)] truncate">Claude Admin</span>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-secondary)]"
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
                  ? "bg-[var(--primary-light)] text-[var(--primary)] font-medium border-r-2 border-[var(--primary)]"
                  : "text-[var(--text-secondary)] hover:bg-[var(--bg)] hover:text-[var(--text-primary)]"
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
      <div className="border-t border-[var(--border)] px-3 py-3">
        {!collapsed && user && (
          <div className="mb-2 truncate text-xs text-[var(--text-muted)]">{user.name}</div>
        )}
        <button
          onClick={logout}
          className={`flex items-center gap-2 rounded-md text-sm text-[var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)] transition-colors ${
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
