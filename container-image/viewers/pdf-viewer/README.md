# PDF Viewer — react-pdf 기반

Pod 내에서 민감 PDF 파일을 다운로드 없이 브라우저에서 보기 위한 뷰어.

## 사용법
OnlyOffice에서 PDF도 지원하지만, 가벼운 PDF 전용 뷰어가 필요한 경우 사용.

## 구현 방향
- react-pdf (@react-pdf/renderer) 또는 pdf.js 기반
- auth-gateway에서 파일 콘텐츠를 스트리밍 (S3 직접 접근 불가)
- Content-Disposition: inline (attachment 아님 — 다운로드 차단)
- Content-Security-Policy: sandbox 적용
