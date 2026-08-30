/**
 * 룩 관련 목업 사진.
 *
 * 홈의 '오늘의 룩'과 룩상세는 같은 룩을 가리키므로 같은 사진을 써야 한다.
 * (백엔드가 붙으면 today_look.image 가 내려오고, 룩상세도 같은 값을 파라미터로 받게 된다.
 *  그때까지는 이 상수가 둘을 이어주는 자리다.)
 */
export const TODAY_LOOK_IMAGE = require('../../assets/images/mock/today-look.jpg');

/** 가상 착장 결과 — 원본 룩이 아니라 '내 체형에 입힌 결과'라 다른 사진을 쓴다. */
export const FITTING_RESULT_IMAGE = require('../../assets/images/mock/fitting-result.png');

/**
 * 프로필 사진 목업 — 사진 업로드가 붙기 전까지 쓰는 기본 아바타.
 * 실제 인물 사진 대신 수채 일러스트를 쓴다(모르는 사람 얼굴을 기본값으로 두지 않으려고).
 */
export const PROFILE_IMAGE = require('../../assets/images/mock/profile.jpg');
