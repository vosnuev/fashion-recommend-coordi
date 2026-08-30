/**
 * 옷장 태그 체계 — 백엔드 `api/apps/wardrobe/taxonomy.py` 의 거울.
 *
 * 저장·필터에 쓰는 값은 **한글 라벨 그대로**다(코드값이 따로 없다). 이미지 프로세서의
 * 캡셔닝도 같은 라벨을 돌려주고, PATCH 는 이 목록에 없는 값을 400 으로 거른다.
 * ⚠️ 백엔드 taxonomy.py 가 바뀌면 이 파일도 함께 고쳐야 한다(양쪽이 같은 목록을 봐야 함).
 */

export const CATEGORY_LARGE = [
  '상의',
  '하의',
  '아우터',
  '원피스/세트',
  '신발',
  '가방',
  '액세서리',
  '언더웨어/이너웨어',
] as const;

export type CategoryLarge = (typeof CATEGORY_LARGE)[number];

export const CATEGORY_SMALL: Record<CategoryLarge, readonly string[]> = {
  상의: ['티셔츠', '셔츠/블라우스', '니트/스웨터', '후드/맨투맨', '민소매'],
  하의: ['데님 팬츠', '슬랙스', '코튼 팬츠', '트레이닝 팬츠', '숏팬츠', '스커트', '레깅스'],
  아우터: ['자켓', '코트', '패딩', '점퍼/블루종', '가디건', '후드집업', '베스트'],
  '원피스/세트': ['원피스', '점프수트/오버롤', '셋업', '파자마/홈웨어 세트'],
  신발: ['스니커즈', '구두/로퍼', '부츠', '샌들/슬리퍼', '플랫/단화'],
  가방: ['백팩', '크로스백', '숄더백', '토트백', '에코백', '클러치/파우치', '지갑'],
  액세서리: ['모자', '벨트', '주얼리', '머플러/스카프', '양말', '안경/선글라스', '헤어 액세서리'],
  '언더웨어/이너웨어': ['브라', '팬티/드로즈', '런닝/캐미솔', '속바지', '보정속옷', '내복/발열 이너'],
};

export const STYLES = [
  '캐주얼', '포멀', '미니멀', '스트릿', '스포티', '러블리', '페미닌',
  '시크', '빈티지', '아웃도어', '댄디', '아메카지', '트렌디', '리조트', '베이직',
] as const;

export const COLORS = [
  '화이트', '블랙', '그레이', '네이비', '블루', '스카이블루', '레드', '핑크',
  '오렌지', '옐로우', '그린', '카키', '브라운', '베이지', '아이보리', '퍼플', '멀티',
] as const;

export const PATTERNS = ['무지', '체크', '스트라이프', '도트', '플로럴', '그래픽/로고', '카모', '애니멀'] as const;

export const FITS = ['오버핏', '레귤러핏', '슬림핏', '와이드핏'] as const;

export const MATERIALS = [
  '코튼', '데님', '니트', '울', '린넨', '레더', '나일론', '폴리에스터',
  '시폰', '코듀로이', '트위드', '퍼/무스탕', '패딩충전재',
] as const;

export const SLEEVES = ['반팔', '긴팔', '민소매'] as const;

export const LENGTHS = ['크롭', '기본', '롱'] as const;

export const SEASONS = ['봄', '여름', '가을', '겨울', '간절기'] as const;

export const LAYER_ROLES = ['기본 상의', '레이어드 상의', '아우터'] as const;

/** 대분류-소분류 짝 정합성 — PATCH 전에 프론트에서 미리 걸러 400 을 줄인다. */
export function isValidCategoryPair(large: string, small: string): boolean {
  if (!small) return true;
  const smalls = CATEGORY_SMALL[large as CategoryLarge];
  return smalls ? smalls.includes(small) : false;
}

/** 옷장 필터 칩용 — '전체' + 대분류 8종 */
export const WARDROBE_FILTER_OPTIONS = ['전체', ...CATEGORY_LARGE];
