
### 2. 안전관리 DB (Safety)

```bash
# 올바른 방법
psql $DATABASE_URL -c "쿼리"
```

- 읽기 전용 (SELECT만 가능)
- DB: safety
- 테이블 목록: `psql $DATABASE_URL -c "\dt"`

**주요 테이블 (업무별)**:

| 분류 | 테이블 | 설명 |
|------|--------|------|
| **TBM** | `safety_activity_tbmactivity` | TBM 활동 기록 |
| | `safety_activity_tbmactivity_companion` | TBM 동행자 |
| | `safety_activity_tbmactivityimages` | TBM 사진 |
| **작업정보** | `safety_activity_workinfo` | 작업 정보 (region_sko, team 등) |
| | `safety_activity_workstatus` | 작업 상태 |
| | `safety_activity_workstatushistory` | 작업 상태 이력 |
| | `safety_activity_worktype` | 작업 유형 |
| **작업중지** | `safety_activity_workstophistory` | 작업 중지 이력 |
| | `safety_activity_workstophistoryimages` | 작업 중지 사진 |
| **순찰점검** | `safety_activity_patrolsafetyinspection` | 순찰 안전점검 |
| | `safety_activity_patrolsafetyinspectchecklist` | 점검 체크리스트 |
| | `safety_activity_patrolsafetyinspectiongoodandbad` | 양호/불량 판정 |
| | `safety_activity_patrolsafetyjointinspection` | 합동 점검 |
| **주간계획** | `safety_activity_weeklyworkplanfrombp` | BP별 주간 작업계획 |
| | `safety_activity_weeklyworkplanperskoregion` | SKO 담당별 주간계획 |
| | `safety_activity_weeklyworkplanperskoteam` | SKO 팀별 주간계획 |
| **안전등급(SHE)** | `she_measurement_sherecord` | SHE 측정 기록 |
| | `she_measurement_shecategory` | SHE 카테고리 |
| | `she_measurement_sheitemscore` | SHE 항목 점수 |
| **컴플라이언스** | `compliance_check_checklistrecord` | 컴플라이언스 점검 기록 |
| | `compliance_check_checklistitem` | 점검 항목 |
| **위험성평가** | `committee_workriskassessment` | 작업 위험성 평가 |
| **게시판** | `board_post` | 게시글 |
| | `board_comment` | 댓글 |
| | `board_file` | 첨부파일 |
| **사용자** | `auth_user` | Django 사용자 (username=사번) |
| | `accounts_userprofile` | 사용자 프로필 (region_name, team_name, job_name) |
| **조직** | `sysmanage_region` | 담당 조직 |
| | `sysmanage_teamregion` | 팀 조직 |
| | `sysmanage_companymaster` | 협력사 마스터 |

**자주 쓰는 Safety 쿼리**:
```sql
-- 오늘 TBM 건수 (담당별)
psql $DATABASE_URL -c "SELECT w.region_sko, COUNT(*) FROM safety_activity_tbmactivity t JOIN safety_activity_workinfo w ON t.work_id_id = w.id WHERE DATE(t.created_at) = CURRENT_DATE GROUP BY w.region_sko ORDER BY COUNT(*) DESC;"

-- 작업 현황
psql $DATABASE_URL -c "SELECT status, COUNT(*) FROM safety_activity_workstatus GROUP BY status;"

-- 순찰점검 현황
psql $DATABASE_URL -c "SELECT COUNT(*) FROM safety_activity_patrolsafetyinspection WHERE DATE(created_at) = CURRENT_DATE;"
```
