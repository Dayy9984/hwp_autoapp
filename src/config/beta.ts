// 베타 전역 플래그.
//
// IS_BETA: true 이면 베타 빌드 — Codex 전용 / 텔레메트리 전송 / 만족도 모달 노출.
// 정식 빌드 시 false 또는 이 파일 제거.

export const IS_BETA = true

// Codex CLI 만 노출 — API Key 모드 UI 숨김 + connectionMode 강제.
export const BETA_CODEX_ONLY = IS_BETA

// Worker /track 으로 텔레메트리 전송.
export const TELEMETRY_ENDPOINT = 'https://inserty-beta-worker.snsoffice.workers.dev/track'

// Tally 폼 임베드 URL — 10개 폼.
export const TALLY_FORMS = {
  onboarding_short: 'https://tally.so/embed/0QaxON?alignLeft=1&hideTitle=0&transparentBackground=1',
  satisfaction_accept: 'https://tally.so/embed/zxLqYa?alignLeft=1&hideTitle=0&transparentBackground=1',
  satisfaction_reject: 'https://tally.so/embed/5BMzx6?alignLeft=1&hideTitle=0&transparentBackground=1',
  weekly_usability: 'https://tally.so/embed/dWv6xq?alignLeft=1&hideTitle=0&transparentBackground=1',
  price_intent: 'https://tally.so/embed/D4MNdq?alignLeft=1&hideTitle=0&transparentBackground=1',
  nps: 'https://tally.so/embed/lbvydB?alignLeft=1&hideTitle=0&transparentBackground=1',
  purchase_intent: 'https://tally.so/embed/RGk05d?alignLeft=1&hideTitle=0&transparentBackground=1',
  feature_request: 'https://tally.so/embed/obvyMe?alignLeft=1&hideTitle=0&transparentBackground=1',
  bug_report: 'https://tally.so/embed/ODkz57?alignLeft=1&hideTitle=0&transparentBackground=1',
} as const
