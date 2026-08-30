/**
 * 도움말·약관 화면이 쓰는 문구 모음.
 *
 * 화면 코드와 떼어 둔 이유: 이 내용은 개발이 아니라 **팀·법무가 확정할 문서**다.
 * 문구가 바뀔 때 화면 코드를 건드리지 않도록 여기만 고치게 한다.
 *
 * ⚠️ 아래 개인정보 항목은 **앱이 실제로 하는 일**을 적은 것이다(A6 권한 화면·옷장 업로드·
 *    체형 촬영과 내용이 일치해야 한다). 앱 동작을 바꾸면 이 파일도 같이 고칠 것.
 * ⚠️ 법적 검토 전 초안이다. 배포 전 팀 확인 필요.
 */

/** 문의처 — 팀 계정이 정해지면 바꾼다(현재는 자리 표시용 주소). */
export const SUPPORT_EMAIL = 'cozy.help@example.com';

export const APP_VERSION = 'v0.1.0';

export type FaqItem = { q: string; a: string };

export const FAQ: FaqItem[] = [
  {
    q: '옷을 등록했는데 옷장에 바로 안 보여요',
    a: '사진에서 옷을 분리하고 태그를 다는 처리가 서버에서 돌아가요. 보통 1분 안에 끝나고, 그동안 옷장 위쪽에 진행 상황이 보여요. 화면을 닫아도 처리는 계속돼요.',
  },
  {
    q: '추천이 제 취향과 안 맞아요',
    a: '마이 > 추구미·선호도에서 좋아하는 무드와 피하고 싶은 것을 다시 고를 수 있어요. 룩북에서 마음에 드는 룩에 좋아요를 누르면 비슷한 룩을 더 보여드려요.',
  },
  {
    q: '예산에 맞는 상품만 보고 싶어요',
    a: '마이 > 예산에서 상의·하의·아우터처럼 카테고리별 상품 한 개의 최대 가격을 정할 수 있어요. 입력하지 않은 카테고리는 서비스 기본 가격을 적용해 너무 비싼 상품을 제외해요.',
  },
  {
    q: '체형 사진은 어디에 쓰이나요',
    a: '가상 피팅에서 몸에 맞는 실루엣을 그리는 데만 써요. 촬영을 건너뛰고 치수를 직접 입력해도 되고, 사진은 90일 뒤 자동으로 지워져요.',
  },
  {
    q: '상품을 눌렀더니 쇼핑몰 검색 결과가 나와요',
    a: '아직 상품 하나하나의 주소를 받아오지 못해서, 브랜드와 상품명으로 검색한 결과로 보내드리고 있어요. 준비되는 대로 상품 페이지로 바로 가게 바꿀게요.',
  },
];

/** 개인정보 처리방침 — 실제 동작 기준. */
export const PRIVACY_SECTIONS: { title: string; body: string }[] = [
  {
    title: '무엇을 받나요',
    body: '소셜 로그인(네이버·카카오·구글)으로 받은 이메일과 닉네임, 그리고 사용자가 직접 넣은 것들이에요 — 옷 사진, 체형 치수와 촬영 사진, 추구미·예산 설정.',
  },
  {
    title: '어디에 쓰나요',
    body: '옷 사진은 배경을 지우고 색·소재·핏 같은 태그를 달아 옷장을 만드는 데, 체형 정보는 가상 피팅에, 설정값은 추천 순서를 정하는 데 써요. 그 밖의 용도로는 쓰지 않아요.',
  },
  {
    title: '얼마나 두나요',
    body: '체형 촬영 사진은 90일 뒤 자동으로 지워져요. 옷 사진과 설정은 계정을 지울 때까지 남고, 옷장에서 개별 삭제할 수 있어요.',
  },
  {
    title: '위치 정보',
    body: '오늘 날씨에 맞는 옷을 고르려고 현재 위치를 쓰지만, 조회에만 쓰고 저장하지 않아요. 권한을 주지 않으면 지역을 직접 고르면 돼요.',
  },
  {
    title: '누구에게 넘기나요',
    body: '광고·마케팅 목적으로 개인정보를 파는 일은 없어요. 사진 저장과 이미지 처리에 클라우드 사업자를 쓰고, 그 범위에서만 위탁해요.',
  },
  {
    title: '내 정보를 지우려면',
    body: `마이 > 계정 관리에서 탈퇴할 수 있어요. 개별 항목은 옷장·설정에서 바로 지울 수 있고, 문의는 ${SUPPORT_EMAIL} 로 보내주세요.`,
  },
];

/** 이용약관 — 서비스 성격에 맞춘 초안. */
export const TERMS_SECTIONS: { title: string; body: string }[] = [
  {
    title: '어떤 서비스인가요',
    body: 'cozy 는 가진 옷과 날씨·취향을 바탕으로 입을 옷을 제안하는 서비스예요. 옷을 직접 팔지 않고, 구매는 각 쇼핑몰에서 이뤄져요.',
  },
  {
    title: '추천에 대해',
    body: '추천은 참고를 위한 제안이에요. 색·핏·사이즈가 실제 착용감과 다를 수 있고, 특히 가상 피팅은 치수를 바탕으로 한 예상 이미지예요.',
  },
  {
    title: '상품 정보와 구매',
    body: '가격·재고·배송은 판매처 기준이고 우리가 보여주는 값과 다를 수 있어요. 구매·결제·교환·환불은 해당 쇼핑몰의 약관을 따라요.',
  },
  {
    title: '올리는 사진에 대해',
    body: '옷 사진과 룩은 본인이 권리를 가진 것만 올려주세요. 룩북에 올린 것은 다른 사용자에게 보일 수 있고, 언제든 지울 수 있어요.',
  },
  {
    title: '계정',
    body: '계정은 소셜 로그인으로 만들어요. 다른 사람과 계정을 함께 쓰면 옷장·추천이 뒤섞이니 권하지 않아요.',
  },
];

/**
 * 오픈소스 고지 — 앱이 쓰는 주요 라이브러리.
 * package.json 의존성과 어긋나지 않게, 새 라이브러리를 넣으면 여기도 함께 갱신할 것.
 */
export const OSS_LICENSES: { name: string; license: string }[] = [
  { name: 'React Native', license: 'MIT' },
  { name: 'React', license: 'MIT' },
  { name: 'Expo · Expo Router', license: 'MIT' },
  { name: 'React Navigation', license: 'MIT' },
  { name: 'react-native-reanimated', license: 'MIT' },
  { name: 'react-native-gesture-handler', license: 'MIT' },
  { name: 'react-native-safe-area-context', license: 'MIT' },
  { name: 'react-native-screens', license: 'MIT' },
  { name: 'react-native-svg', license: 'MIT' },
  { name: 'react-native-webview', license: 'MIT' },
  { name: 'Ionicons (@expo/vector-icons)', license: 'MIT' },
  { name: '@react-native-seoul/naver-login', license: 'MIT' },
  { name: '@react-native-kakao/core · user', license: 'MIT' },
  { name: '@react-native-google-signin/google-signin', license: 'MIT' },
];
