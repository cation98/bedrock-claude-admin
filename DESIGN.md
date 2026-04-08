# Design System — Bedrock AI Platform

## Product Context
- **What this is:** AWS Bedrock 기반 사내 Claude Code 활용 플랫폼. 데이터 거버넌스 + 보안 통제 포함.
- **Who it's for:** 사내 임원/팀장/실무자 (50명 전사 운영)
- **Space/industry:** Enterprise Internal Developer Platform, AI Coding Tool
- **Project type:** Internal tool / Admin dashboard / Web terminal

## Aesthetic Direction
- **Direction:** Industrial/Utilitarian
- **Decoration level:** Minimal — 타이포그래피와 공간이 모든 것을 함. 장식적 요소 없음.
- **Mood:** 신뢰할 수 있는 전문 도구. 보안 플랫폼답게 차분하고 명확함. 화려함보다 정확함.
- **Reference sites:** Vercel Dashboard, Linear, GitHub

## Typography
- **Display/Hero:** Geist (Vercel) — 기술적 전문성, 현대적
- **Body:** Geist — 동일 폰트로 일관성 유지
- **UI/Labels:** Geist — same as body
- **Data/Tables:** Geist (font-variant-numeric: tabular-nums) — 숫자 정렬 필수
- **Code:** Geist Mono — 터미널, 코드 블록, secure-get 출력
- **Korean:** Pretendard Variable — 한글 최적화 (SIL Open Font License)
- **Loading:**
  - Geist: `https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-sans/style.min.css`
  - Geist Mono: `https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-mono/style.min.css`
  - Pretendard: `https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css`
- **Font stack:** `'Geist', 'Pretendard Variable', -apple-system, BlinkMacSystemFont, sans-serif`
- **Scale:** 11px (caption) / 12px (label) / 13px (body-sm) / 14px (body) / 15px (body-lg) / 18px (title) / 20px (h2) / 24px (h1) / 28px (display) / 32px (hero)

## Color
- **Approach:** Restrained — 1 accent + neutrals. 색상은 의미 있는 곳에만.
- **Primary:** `#2563EB` (blue-600) — 신뢰, 전문성. 링크, 선택됨, 주요 CTA
- **Neutrals:** Cool gray series
  - `#F9FAFB` (gray-50) — page background
  - `#F3F4F6` (gray-100) — hover, sidebar bg
  - `#E5E7EB` (gray-200) — borders
  - `#D1D5DB` (gray-300) — strong borders
  - `#9CA3AF` (gray-400) — muted text, placeholders
  - `#6B7280` (gray-500) — secondary text
  - `#374151` (gray-700) — dark surface (dark mode)
  - `#1F2937` (gray-800) — dark surface
  - `#111827` (gray-900) — primary text
- **Semantic:**
  - Sensitive/Danger: `#DC2626` (red-600) — 민감 파일 표시 전용. 일반 에러에는 사용하지 않음.
  - Warning: `#D97706` (amber-600) — 만료 임박, 주의 필요
  - Success: `#059669` (emerald-600) — 정상, 완료, 인증 성공
  - Info: `#0891B2` (cyan-600) — 분류 중, 정보성 안내
  - Error (non-sensitive): `#9333EA` (purple-600) — 시스템 에러 (민감과 구분)
- **Light backgrounds:** 각 semantic color의 50/100 변형 사용 (e.g., `#FEE2E2` for danger-light)
- **Dark mode:** 배경을 gray-900/800로, 텍스트를 gray-50/100으로 반전. Semantic colors는 채도 10% 높임.

## Spacing
- **Base unit:** 4px
- **Density:** Comfortable (대시보드 테이블은 약간 dense)
- **Scale:**
  - 2xs: 2px / xs: 4px / sm: 8px / md: 16px / lg: 24px / xl: 32px / 2xl: 48px / 3xl: 64px
- **Component padding:** Card: 20px, Table cell: 10px 16px, Button: 8px 16px, Input: 8px 12px

## Layout
- **Approach:** Grid-disciplined — 엄격한 컬럼 정렬, 예측 가능한 레이아웃
- **Grid:** Admin: sidebar(220px) + main content. Main content: max 1200px centered.
- **Max content width:** 1200px
- **Border radius:**
  - sm: 4px (badges, small elements)
  - md: 6px (buttons, inputs, alerts)
  - lg: 8px (cards, panels, modals)
  - full: 9999px (pills, circular badges)
- **Sidebar:** 220px width, dark bg in dark mode / gray-50 in light mode, 1px border-right

## Motion
- **Approach:** Minimal-functional — 상태 전환만. 장식적 애니메이션 없음.
- **Easing:** enter: ease-out / exit: ease-in / move: ease-in-out
- **Duration:** micro: 75ms / short: 150ms / medium: 250ms
- **Usage:** 호버 상태 전환(75ms), 모달 열림/닫힘(150ms), 페이지 전환(없음 — instant)
- **금지:** 스크롤 애니메이션, 입장 애니메이션, 바운스 효과, 장식적 전환

## Design Tokens (CSS Custom Properties)
```css
:root {
  --bg: #F9FAFB;
  --surface: #FFFFFF;
  --surface-hover: #F3F4F6;
  --border: #E5E7EB;
  --border-strong: #D1D5DB;
  --text-primary: #111827;
  --text-secondary: #6B7280;
  --text-muted: #9CA3AF;
  --primary: #2563EB;
  --primary-hover: #1D4ED8;
  --primary-light: #DBEAFE;
  --danger: #DC2626;
  --danger-light: #FEE2E2;
  --warning: #D97706;
  --warning-light: #FEF3C7;
  --success: #059669;
  --success-light: #D1FAE5;
  --info: #0891B2;
  --info-light: #CFFAFE;
  --error: #9333EA;
  --error-light: #F3E8FF;
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
  --font-sans: 'Geist', 'Pretendard Variable', -apple-system, sans-serif;
  --font-mono: 'Geist Mono', monospace;
}
```

## Component Patterns
- **Stat Cards:** 숫자가 가장 크고 (28px bold), 라벨이 작고 (12px muted). 위험 수치는 danger 색상.
- **Data Tables:** 좌정렬, 12px uppercase 헤더, 13px 본문, hover 배경. 민감 파일은 badge로 표시.
- **Badges:** pill shape (border-radius: full), semantic color background + foreground text. 아이콘 포함.
- **Buttons:** primary(blue), secondary(outlined), danger(red), ghost(text-only). 8px 16px padding.
- **Alerts:** full-width, left-aligned icon + text, semantic color background. border-radius: md.
- **Sidebar Navigation:** 세로 리스트, active 상태는 primary-light 배경 + primary 텍스트.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-09 | Initial design system created | /design-consultation based on product context (enterprise security platform) |
| 2026-04-09 | Geist + Pretendard 선택 | 기술 제품 전문성(Geist) + 한글 최적화(Pretendard) |
| 2026-04-09 | Red = sensitive only (not error) | 빨간색이 "민감 데이터"를 즉각 의미하도록. 시스템 에러는 purple 사용. |
| 2026-04-09 | Minimal motion | 보안 플랫폼은 안정감이 우선. 장식적 애니메이션은 신뢰를 해침. |
