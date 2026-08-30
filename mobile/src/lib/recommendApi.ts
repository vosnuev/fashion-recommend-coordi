import { RecommendEndpoints } from '@/constants/config';
import { api, ApiError } from '@/lib/apiClient';

/**
 * 추천 결과 조회.
 *
 * 채팅 답변이 추천까지 만들어 내면 그 답변 메시지의 metadata 에 recommendation_result_id 만
 * 들어온다. 실제 코디 구성(카드)은 여기서 따로 받아 온다 — 대화 API 는 대화만 다룬다.
 */

export type ApiRecommendationItem = {
  item_id: string;
  position: number;
  slot: string;
  /** WARDROBE = 내 옷장 옷, PRODUCT = 새로 살 상품 */
  source_type: string;
  /**
   * 상품 카탈로그의 원본 식별자. 상품을 이름이 아니라 이 값으로 구분한다 —
   * 같은 상품이 카드마다 다른 이름(스냅샷)으로 오기 때문이다. 지금 화면에서 쓰지는 않는다.
   */
  source_id: string;
  display_name: string;
  category: string | null;
  color: string | null;
  /**
   * ⚠️ **바로 쓸 수 있는 주소가 아닐 수 있다.** 백엔드 주석이 "S3 키 또는 검증된 URL" 이라,
   *    옷장 아이템은 키가, 상품은 URL 이 들어오는 식으로 섞인다. 화면에는 image_url 을 쓴다.
   */
  image_ref: string;
  /**
   * 화면에 바로 걸 수 있는 주소. 서버가 조회 시점에 만들어 주는 presigned URL 이라
   * **만료된다** — 응답을 오래 들고 있다가 그리지 말고, 캐시에 굽지도 않는다.
   * 만들 수 없으면 null 이고, 서버가 아직 이 필드를 모르면 undefined 다(구버전 호환).
   */
  image_url?: string | null;
  /** 옷장 아이템이면 null (살 필요가 없으므로 가격이 없다) */
  price_snapshot: number | null;
  purchase_url: string | null;
  reasons: string[];
  /** 상세 화면에서 보여줄 개별 아이템 선택 이유. 기존 추천이면 빈 문자열이다. */
  note: string;
};

/** 검증기가 남긴 한 줄. severity 는 INFO/WARNING 계열 문자열이다. */
export type ApiValidationReason = {
  severity: string;
  code: string;
  message: string;
  slot: string | null;
};

/**
 * 공유 옷을 참고해 만든 카드에 붙는 매칭 근거.
 *
 * 참고하지 않은 추천은 **빈 객체 `{}`** 로 온다(null 이 아니다) — 그래서 `match_type` 유무로 가른다.
 *
 * ⚠️ 값이 늘 수 있으므로 `match_type`·`source_type` 을 좁은 유니온으로 못 박지 않는다.
 *    모르는 값이 오면 배지를 **생략**하고 카드 자체는 그대로 그린다(요구사항 7장).
 * ⚠️ `score` 는 화면에 그대로 노출하지 않는다. 사용자에게는 뜻이 없는 숫자다.
 */
export type ApiReferenceMatch = {
  schema_version?: string;
  /** 'VISUAL_SIMILAR' | 'STYLE_SIMILAR' — 그 밖의 값이 오면 배지를 생략한다 */
  match_type?: string;
  selection_role?: string;
  /** 'WARDROBE' | 'PRODUCT' */
  source_type?: string;
  source_id?: string;
  source_collection?: string;
  source_point_id?: string;
  template_item_point_id?: string;
  score?: number;
  /** 상세 화면에서만 보여줄 근거 문장 */
  reasons?: string[];
};

export type ApiRecommendationCard = {
  card_id: string;
  rank: number;
  /** 새로 사야 하는 상품들의 합. 옷장 옷만으로 짠 코디면 0 이다. */
  total_product_price: number | null;
  warnings: string[];
  /** 채팅 카드 바로 아래에서 보여줄 코디 전체 추천 이유. */
  rationale: string;
  /** 공유 옷 참고 결과. 참고 안 했으면 빈 객체다. 서버가 이 필드를 아예 안 줄 수도 있다. */
  reference_match?: ApiReferenceMatch;
  items: ApiRecommendationItem[];
  validation_reasons: ApiValidationReason[];
  /** 아직 반응을 남기지 않았으면 null. 카드 목록·상세가 같은 모양으로 준다. */
  feedback: ApiCardFeedback | null;
};

export type ApiRecommendationResult = {
  result_id: string;
  session_id: string;
  run_id: string;
  mode: string;
  created_at: string;
  cards: ApiRecommendationCard[];
};

/**
 * 화면에 걸 수 있는 주소만 통과시킨다.
 * S3 키(예: "wardrobe/2026/ab12.jpg")를 그대로 <Image> 에 넘기면 조용히 깨진 자리만 남는다.
 */
export function imageUrlOf(imageRef: string | null | undefined): string | null {
  if (!imageRef) return null;
  return /^https?:\/\//.test(imageRef) ? imageRef : null;
}

/**
 * 추천 아이템 사진 주소. 서버가 주는 image_url 을 먼저 쓰고, 없으면 image_ref 로 물러선다.
 *
 * image_ref 폴백을 남기는 이유: 이 필드가 없던 시절의 응답(캐시·목업)과, 애초에 http 주소가
 * 들어 있던 옛 상품 데이터가 아직 있다. 둘 다 아니면 null 이고 자리표시자가 뜬다.
 */
export function itemImageUrl(item: {
  image_url?: string | null;
  image_ref?: string | null;
}): string | null {
  return imageUrlOf(item.image_url) ?? imageUrlOf(item.image_ref);
}

export function getRecommendationResult(resultId: string): Promise<ApiRecommendationResult> {
  return api.get<ApiRecommendationResult>(RecommendEndpoints.result(resultId));
}

/** 카드 한 장. 목록에도 같은 모양이 들어 있지만, 상세는 항상 최신 피드백을 다시 받는다. */
export function getRecommendationCard(
  resultId: string,
  cardId: string,
): Promise<ApiRecommendationCard> {
  return api.get<ApiRecommendationCard>(RecommendEndpoints.card(resultId, cardId));
}

/* ── 피드백 ───────────────────────────────────────── */

export type ApiFeedbackReaction = 'LIKE' | 'DISLIKE';

export type ApiCardFeedback = {
  feedback_id: string;
  reaction: ApiFeedbackReaction;
  reason_codes: string[];
  comment: string;
  created_at: string;
  updated_at: string;
};

/**
 * 왜 별로였는지 고르는 코드. 서버는 대문자 코드면 무엇이든 받지만, 집계가 되려면
 * 값이 흔들리지 않아야 해서 앱이 쓰는 목록을 여기 고정한다.
 */
export const FEEDBACK_REASONS = [
  { code: 'STYLE', label: '스타일이 안 맞아요' },
  { code: 'COLOR', label: '색이 취향이 아니에요' },
  { code: 'FIT', label: '핏이 안 맞아요' },
  { code: 'PRICE', label: '너무 비싸요' },
  { code: 'ALREADY_OWNED', label: '이미 비슷한 옷이 있어요' },
] as const;

/**
 * 카드의 최신 반응을 통째로 교체한다(PUT). 사유를 바꾸려면 reaction 도 함께 보낸다.
 * 서버가 카드당 하나만 두므로 여러 번 보내도 마지막 것만 남는다.
 */
export function putCardFeedback(
  resultId: string,
  cardId: string,
  input: { reaction: ApiFeedbackReaction; reasonCodes?: string[]; comment?: string },
): Promise<ApiCardFeedback> {
  return api.put<ApiCardFeedback>(RecommendEndpoints.cardFeedback(resultId, cardId), {
    reaction: input.reaction,
    reason_codes: input.reasonCodes ?? [],
    comment: input.comment ?? '',
  });
}

/** 반응 취소. 카드 자체는 그대로 남는다. */
export function deleteCardFeedback(resultId: string, cardId: string): Promise<void> {
  return api.delete<void>(RecommendEndpoints.cardFeedback(resultId, cardId));
}

/* ── 코디 이미지 ───────────────────────────────────── */

export type ApiRenderStatus = 'QUEUED' | 'PROCESSING' | 'SUCCEEDED' | 'FAILED';

export type ApiRenderJob = {
  job_id: string;
  card_id: string;
  status: ApiRenderStatus;
  cache_hit: boolean;
  /** 만료되는 presigned URL. SUCCEEDED 일 때만 채워진다. */
  image_url: string | null;
  error: { code: string; message: string } | null;
  created_at: string;
  updated_at: string;
};

export function isRenderTerminal(status: ApiRenderStatus): boolean {
  return status === 'SUCCEEDED' || status === 'FAILED';
}

/**
 * 이미지 생성 상태. **아직 작업이 없으면 null** 이다(서버는 404).
 * 추천이 저장될 때 서버가 미리 작업을 걸어두므로 보통은 여기서 결과가 나온다.
 */
export async function getCardRender(
  resultId: string,
  cardId: string,
): Promise<ApiRenderJob | null> {
  try {
    return await api.get<ApiRenderJob>(RecommendEndpoints.cardRender(resultId, cardId));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** 이미지 생성 접수. 이미 만들어 둔 같은 조합이 있으면 서버가 그대로 돌려준다. */
export function requestCardRender(
  resultId: string,
  cardId: string,
): Promise<ApiRenderJob> {
  return api.post<ApiRenderJob>(RecommendEndpoints.cardRender(resultId, cardId));
}
