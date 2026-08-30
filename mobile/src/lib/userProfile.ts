import type { AuthUser } from '@/state/auth';

/**
 * 표시용 이름과 사진을 한 곳에서 정한다.
 *
 * 예전에는 홈이 이름을 `'코지'` 로 박아 두고 아바타도 번들 목업을 넘겨서, 카카오·네이버·
 * 구글로 들어온 사람도 남의 이름과 사진을 봤다. 서버는 이미 provider 의 닉네임·사진을
 * 저장하고 **로그인할 때마다 갱신**하고 있었는데 화면이 그걸 안 쓰고 있었다.
 */

/** 소셜 가입 시 서버가 자동으로 만드는 username (kakao_123456 등) — 이름으로 쓰면 안 된다. */
const AUTO_USERNAME = /^(naver|kakao|google)_/;

/**
 * 화면에 보일 이름. **없으면 빈 문자열이다 — 지어내지 않는다.**
 *
 * 소셜로 들어왔으면 provider 닉네임을 쓴다. 이메일로 막 가입한 계정에는 아직 이름이 없는데,
 * 예전에는 그 자리를 서비스 이름('코지')으로 채워서 처음 들어온 사람이 남의 이름을 자기
 * 프로필로 봤다. 비어 있으면 화면이 '이름 설정하기'로 안내한다(마이) 또는 이름 없이
 * 인사한다(홈). 이메일 앞부분을 쓰지 않는 것은 팀 결정이다.
 */
export function displayName(user: AuthUser | null | undefined): string {
  const nickname = user?.nickname?.trim();
  if (nickname && !AUTO_USERNAME.test(nickname)) return nickname;
  return '';
}

/**
 * 아바타에 넘길 사진.
 *
 * 서버의 profile_image 는 '내가 올린 사진(presigned URL) → 없으면 소셜 사진' 순으로
 * 이미 정리돼 내려온다. **둘 다 없으면 사진을 넘기지 않는다** — 예전에는 번들 목업 사진으로
 * 떨어져서, 아무것도 올린 적 없는 계정이 남의 얼굴을 자기 프로필로 달고 있었다.
 * 사진이 없으면 Avatar 가 모노그램(또는 사람 아이콘)으로 그린다.
 * ⚠️ presigned URL 은 만료되므로(기본 1시간) 오래 캐시하지 말 것.
 */
export function profilePhoto(user: AuthUser | null | undefined): {
  uri?: string;
  asset?: number;
} {
  const uri = user?.profile_image?.trim();
  return uri ? { uri } : {};
}
