/**
 * SK AI Plugin Configuration
 *
 * 이 파일은 Kubernetes ConfigMap (sk-ai-plugin-config)이 생성하는 파일입니다.
 * - 운영 환경: ConfigMap → initContainer → OO 플러그인 디렉토리에 복사
 * - 로컬 개발: index.html이 로드 실패해도 code.js 기본값으로 동작
 *
 * 변수:
 *   aiEndpoint  — auth-gateway AI endpoint URL
 *                 (auth-gateway와 OO가 같은 도메인이면 상대경로 사용 가능)
 *   model       — Bedrock 모델 ID (사용자 표시명)
 *
 * 생성: infra/k8s/platform/onlyoffice.yaml ConfigMap sk-ai-plugin-config 참조
 */
window.SKAI_CONFIG = {
  aiEndpoint: "${SKAI_AI_ENDPOINT}",
  model:      "${SKAI_MODEL}"
};
