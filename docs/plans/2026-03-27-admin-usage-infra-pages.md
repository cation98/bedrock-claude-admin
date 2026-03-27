# Admin Dashboard: Token Usage & Infrastructure Pages

**Goal:** Admin dashboard에 사용자별 토큰 사용량(일/월 누적 + 비용) 페이지와 실시간 노드/Pod 현황 페이지를 추가한다.

**Architecture:** Auth Gateway에 2개 API 엔드포인트 추가. Admin Dashboard에 2개 페이지 추가 (/usage, /infra).

**Tasks:**
1. Backend: admin.py router (token-usage + infrastructure endpoints)
2. Frontend: API types in lib/api.ts
3. Frontend: /usage page
4. Frontend: /infra page
5. Navigation update
6. Build and deploy
