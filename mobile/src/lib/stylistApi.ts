import { ALLOW_STYLIST_MOCK, ChatEndpoints, RecommendEndpoints } from '@/constants/config';
import { api, ApiError } from '@/lib/apiClient';
import type { ApiChatRun } from '@/lib/chatApi';
import {
  getCardRender,
  requestCardRender,
  type ApiRecommendationItem,
  type ApiReferenceMatch,
  type ApiRenderJob,
} from '@/lib/recommendApi';
import { stylistMock } from '@/lib/stylistMock';

/**
 * 스타일리스트 모드 API 의 원형(DTO)과 호출 함수.
 *
 * 모양은 백엔드 브랜치(origin/feature/chat-main-integration)의
 * api/apps/chat/serializers.py 를 그대로 옮긴 것이다. 산문 요약이 아니라 그 코드가 기준이다.
 *
 * ⚠️ **이 엔드포인트들은 배포 서버·main 에 아직 없다.** 라우트가 없으면 404 가 오는데,
 *    그때는 lib/stylistMock.ts 가 대신 답한다(probeAndList). 브랜치가 머지되면 첫 호출이
 *    성공하면서 목업은 저절로 꺼진다 — 붙일 때 고칠 코드가 없게 하려는 것.
 */

/**
 * 스타일리스트 id 는 **슬러그**다 ('minimal' | 'experimental' | 'practical').
 * PersonaProfile(UUID)과는 다른 개념이라 서로 넣지 말 것 — 저쪽은 사용자의 추구미 프로필이고,
 * 이쪽은 stylist_personas.json 이 정의한 고정 추천 관점이다.
 * 서버가 페르소나를 늘릴 수 있으므로 유니온이 아니라 string 으로 둔다.
 */
export type StylistId = string;

export type ApiStylist = {
  id: StylistId;
  display_name: string;
  description: string;
  display_order: number;
};

export type ApiStylistCatalog = {
  schema_version: string;
  /** 1 — 0명은 고를 수 없다 */
  min_select: number;
  /** 3 */
  max_select: number;
  /** 한 번도 고른 적 없을 때 켜지는 값 (minimal) */
  default_persona_ids: StylistId[];
  /** 이 회원이 마지막으로 고른 값. 비어 있으면 default 를 쓴다. */
  last_selected_persona_ids: StylistId[];
  stylists: ApiStylist[];
};

export type ApiResponseMode = 'DEFAULT' | 'STYLIST';

/**
 * 스타일리스트 한 명의 실행 상태.
 * ⚠️ 성공은 `SUCCEEDED` 다 — 설계서 예시 JSON 의 `COMPLETED` 는 실제 값이 아니다.
 */
export type ApiPersonaStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';

/** '다른 추천' 요청의 상태. 카드가 이미 있는 채로 진행되므로 실행 상태와 따로 논다. */
export type ApiAlternativeStatus = 'IDLE' | 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';

/**
 * 스타일리스트 한 명이 내놓은 코디 한 벌.
 * ⚠️ 식별자가 `card_id` 다 (`outfit_id` 가 아니다). items 는 추천 API 와 같은 모양을 쓴다.
 */
export type ApiPersonaCard = {
  card_id: string;
  rank: number;
  total_product_price: number | null;
  /**
   * Validator 가 남긴 **문제 목록**이다 — 이름과 달리 '조건을 충족했다'는 근거가 아니다.
   * (recommendation_pipeline.py 가 validation.issues 를 그대로 넣는다.)
   * severity 는 ERROR|WARNING 이고 code 는 REQUIRED_SLOT_MISSING 처럼 전부 결함 이름이다.
   * VALIDATED 카드에는 보통 비어 있거나 WARNING 만 남는다.
   *
   * 화면에서 쓰지 않는다 — 사용자에게 보여줄 주의 문구는 `warnings` 쪽이고, 이것은
   * 검증기 내부 사정이라 카드에 올리면 소음이 된다.
   */
  validation_reasons: { severity: string; code: string; message: string; slot: string }[];
  warnings: string[];
  items: ApiRecommendationItem[];
  /**
   * 공유 옷 참고 결과.
   *
   * ⚠️ **아직 서버가 안 준다.** 기본 추천 카드(RecommendationCardSerializer)에는 있는데
   *    ChatRunPersonaCardSerializer 에는 빠져 있다. 붙으면 배지가 저절로 뜨도록
   *    optional 로 열어 두고, 없으면 화면이 배지를 생략한다.
   */
  reference_match?: ApiReferenceMatch;
  /** 코디 이미지 렌더 작업. 아직 안 만들어졌으면 null. */
  image: ApiRenderJob | null;
  is_saved: boolean;
};

export type ApiPersonaResult = {
  persona_id: StylistId;
  display_name: string;
  /** 카드 순서는 이 값으로 고정한다 — 완료 순서대로 그리면 볼 때마다 자리가 바뀐다. */
  display_order: number;
  status: ApiPersonaStatus;
  result_id: string | null;
  result_type: string | null;
  /** '다른 추천'을 받을 때마다 올라간다. 1 이 최초 추천. */
  generation: number | null;
  previous_result_ids: string[];
  /** 페르소나 관점의 핵심 문장 하나. 실패했거나 아직이면 빈 문자열. */
  message: string;
  /** 상세 근거 — 접힌 영역에 보여줄 검증 근거 코드들 */
  validated_reason_codes: string[];
  card: ApiPersonaCard | null;
  error: { code: string; message: string } | null;
  retry_count: number;
  alternative_status: ApiAlternativeStatus;
  alternative_count: number;
  alternative_error_code: string;
  alternative_error_message: string;
  latency_ms: number;
  started_at: string | null;
  completed_at: string | null;
};

/**
 * STYLIST run. 기본 run 에 모드·선택값·페르소나별 결과가 더해진 모양이다.
 *
 * run 을 만드는 순간 **선택 인원수만큼 results 자리가 먼저 생기고**(전부 PENDING),
 * 끝난 것부터 SUCCEEDED/FAILED 로 바뀐다. 그래서 화면은 인원수만큼 로딩 카드를 먼저 깔 수 있다.
 */
export type ApiStylistRun = ApiChatRun & {
  response_mode: ApiResponseMode;
  persona_ids: StylistId[];
  results: ApiPersonaResult[];
};

/** 재시도·다른추천의 202 응답. run 은 같은 id 라 그대로 다시 폴링하면 된다. */
export type ApiPersonaAction = {
  run: ApiStylistRun;
  events_url: string;
};

/* ── 목업 갈아끼우기 ─────────────────────────────────── */

/**
 * 서버에 이 라우트들이 있는지. null 이면 아직 물어보지 않은 상태다.
 * 한 번 성공(false)으로 굳으면 그 뒤의 404 는 진짜 오류로 취급해 그대로 던진다 —
 * '세션이 없어서 나는 404' 를 라우트가 없는 것으로 오해하면 진짜 버그가 목업 뒤에 숨는다.
 */
let missing: boolean | null = null;

/** 목업으로 넘어갔다는 사실은 한 번만 알린다. 폴링마다 콘솔이 도배되지 않게. */
let warned = false;

function warnOnce() {
  if (warned) return;
  warned = true;
  console.warn(
    '[stylist] 스타일리스트 API 가 이 서버에 없어 목업으로 그린다. ' +
      '(origin/feature/chat-main-integration 이 머지되면 자동으로 실서버를 쓴다)',
  );
}

/** 목업이 그리고 있는지 — 화면이 "미검증" 배지를 띄우는 데 쓴다. */
export function isStylistMocked(): boolean {
  return missing === true;
}

/**
 * 목업으로 갈지 **판정하는 곳은 여기 하나뿐이다.**
 *
 * 왜 이 자리인가 — 404 가 "라우트가 없다"는 뜻인지 확실한 요청은 이것뿐이다. 다른 자리는
 * 전부 id 를 받는다: 없는 세션에 response-mode 를 걸어도 404, 없는 run 에 재시도를 걸어도
 * 404 다. 거기서 판정하면 **라우트가 멀쩡한 서버에서도 목업으로 굳어 버린다.**
 * (지금 화면 흐름상 팝업이 목록을 먼저 부르지만, 호출 순서에 기대는 안전은 다음 변경에
 *  깨진다. 판정 자체를 한 곳으로 좁혀 둔다.)
 */
async function probeAndList(): Promise<ApiStylistCatalog> {
  if (missing === true) return stylistMock.listStylists();
  try {
    const out = await api.get<ApiStylistCatalog>(ChatEndpoints.stylists);
    missing = false;
    return out;
  } catch (e) {
    const routeAbsent = e instanceof ApiError && (e.status === 404 || e.status === 405);
    /* 배포 빌드에서는 404 도 그냥 오류로 올린다 — 목업이 장애를 가리면 사용자가 지어낸
       코디를 진짜로 받아들인다 (constants/config.ts 의 ALLOW_STYLIST_MOCK 주석). */
    if (missing === null && routeAbsent && ALLOW_STYLIST_MOCK) {
      missing = true;
      warnOnce();
      return stylistMock.listStylists();
    }
    throw e;
  }
}

/**
 * 이미 정해진 판정만 따른다 — 여기서는 목업으로 넘기는 결정을 하지 않는다.
 * 아직 안 정해졌으면(`null`) 실서버로 보내고 오류는 그대로 올린다. 그 편이 낫다:
 * 진짜 오류(없는 세션·없는 run)를 목업 뒤에 숨기면 무엇이 잘못됐는지 알 수 없다.
 */
function orMock<T>(real: () => Promise<T>, mock: () => Promise<T>): Promise<T> {
  return missing === true ? mock() : real();
}

/* ── 호출 ───────────────────────────────────────────── */

/**
 * 고를 수 있는 스타일리스트 목록 + 복원할 선택값. 팝업을 열기 전에 부른다.
 * 스타일리스트 기능을 쓰는 첫 호출이고, 목업 판정도 여기서만 한다(probeAndList 주석).
 */
export function listStylists(): Promise<ApiStylistCatalog> {
  return probeAndList();
}

/**
 * 응답 모드 전환. 대화방을 옮기거나 새로 만들지 않고 **다음 질문부터** 적용된다.
 *
 * ⚠️ DEFAULT 로 끌 때는 response_mode 만 보낸다 — 선택값을 함께 보내면 안 된다.
 * ⚠️ STYLIST 로 켤 때 personaIds 를 **생략하면 서버가 복원한다**
 *    (세션 이전값 → 회원 마지막값 → minimal). 빈 배열을 보내는 것과 다르다.
 */
export function updateResponseMode(
  sessionId: string,
  mode: ApiResponseMode,
  personaIds?: StylistId[],
): Promise<{ response_mode: ApiResponseMode; selected_persona_ids: StylistId[] }> {
  const body =
    mode === 'STYLIST' && personaIds
      ? { response_mode: mode, selected_persona_ids: personaIds }
      : { response_mode: mode };
  return orMock(
    () =>
      api.patch<{ response_mode: ApiResponseMode; selected_persona_ids: StylistId[] }>(
        ChatEndpoints.responseMode(sessionId),
        body,
      ),
    () => stylistMock.updateResponseMode(sessionId, mode, personaIds),
  );
}

/**
 * STYLIST run 단건 조회.
 *
 * 기본 run 조회와 같은 자리(/chat/runs/{id}/)지만 results 가 붙어 온다. results 가 없는
 * 서버(=아직 안 붙은 곳)에서는 목업이 대신 채운다 — 그래서 404 뿐 아니라 **필드 없음**도
 * 목업으로 넘기는 조건이다.
 *
 * `hint` 는 **목업 전용**이다. 목업은 run 을 자기가 만든 게 아니라서(진짜 서버가 만든 run 이다)
 * 누가 몇 명 뽑혔는지 알 길이 없어 자리를 못 만든다. 실서버는 이 값을 쓰지 않는다.
 */
export async function getStylistRun(
  runId: string,
  hint?: { personaIds: StylistId[]; question: string },
): Promise<ApiStylistRun> {
  if (missing === true) return stylistMock.getRun(runId, hint);
  const run = await api.get<ApiStylistRun>(ChatEndpoints.run(runId));

  if (Array.isArray(run.results)) {
    missing = false;
    return run;
  }

  // 라우트는 있는데 results 가 없다 = 스타일리스트 기능 이전 버전의 서버다.
  if (ALLOW_STYLIST_MOCK) {
    if (missing === null) {
      missing = true;
      warnOnce();
    }
    return stylistMock.getRun(runId, hint);
  }
  /* 배포 빌드에서는 조용히 목업으로 넘어가지 않는다. 여기서 run 을 그대로 돌려주면
     부르는 쪽이 results.length 에서 터지므로, 무엇이 잘못됐는지 말하고 끝낸다. */
  throw new Error('서버 응답에 스타일리스트 결과가 없어요. 잠시 후 다시 시도해 주세요.');
}

/** 실패한 스타일리스트 한 명만 다시 실행. 본문 없음, 같은 run 을 다시 폴링한다. */
export function retryPersona(runId: string, personaId: StylistId): Promise<ApiPersonaAction> {
  return orMock(
    () => api.post<ApiPersonaAction>(ChatEndpoints.personaRetry(runId, personaId)),
    () => stylistMock.retryPersona(runId, personaId),
  );
}

/**
 * 성공한 스타일리스트에게 다른 코디를 다시 받는다. 지금 카드는 이력으로 보존되고
 * 새 결과가 현재 결과가 된다(generation 이 하나 올라간다).
 */
export function requestAlternative(
  runId: string,
  personaId: StylistId,
): Promise<ApiPersonaAction> {
  return orMock(
    () => api.post<ApiPersonaAction>(ChatEndpoints.personaAlternative(runId, personaId)),
    () => stylistMock.requestAlternative(runId, personaId),
  );
}

/** 고른 코디를 내 룩으로 저장한다. */
export function saveCard(resultId: string, cardId: string): Promise<unknown> {
  return orMock(
    () => api.put<unknown>(RecommendEndpoints.saveCard(resultId, cardId)),
    () => stylistMock.saveCard(resultId, cardId),
  );
}

/** 선택한 스타일리스트 카드 한 장의 이미지 생성 작업을 접수한다. */
export function renderCard(resultId: string, cardId: string): Promise<ApiRenderJob> {
  return orMock(
    () => requestCardRender(resultId, cardId),
    () => stylistMock.renderCard(resultId, cardId),
  );
}

/** 선택한 카드의 이미지 생성 상태를 폴링한다. */
export function getCardRenderStatus(
  resultId: string,
  cardId: string,
): Promise<ApiRenderJob | null> {
  return orMock(
    () => getCardRender(resultId, cardId),
    () => stylistMock.getCardRender(resultId, cardId),
  );
}
