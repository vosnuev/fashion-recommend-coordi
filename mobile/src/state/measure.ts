import { useSyncExternalStore } from 'react';
import { Platform } from 'react-native';

import {
  BODY_MEASURES,
  EDITABLE_MEASURES,
  type BodyMeasureKey,
} from '@/constants/body-measures';
import { API_BASE_URL, BodyEndpoints } from '@/constants/config';
import { ApiError, api } from '@/lib/apiClient';
import {
  measurementRequestFailureState,
  measurementResultSource,
  photoMeasurementFailureState,
} from '@/lib/bodyMeasurementResult';
import { getAccessToken } from '@/lib/secureStore';
import { uploadMultipart } from '@/lib/uploadFile';

/**
 * 체형측정 플로우(STEP1 입력 → STEP2 촬영 → STEP3 결과) 전역 상태.
 *
 * expo-router 는 세 화면이 서로 다른 라우트라 화면 간 공유 부모가 없다.
 * authStore 와 동일한 경량 모듈 스토어(useSyncExternalStore) 로 스텝 간 데이터를 잇는다.
 *
 * 백엔드 연동(팀레포 main, users/body):
 *   - STEP1  "다음"  → PUT   /users/me/body/basic/  { gender, height, weight }  (saveBasic)
 *   - 사진 없이 진행 → POST  /users/me/body/estimate/  { gender?, height?, weight? }
 *                      서버가 학습 모델로 상세 10개를 추정·저장하고 응답에 실어 준다 (estimate)
 *   - STEP2  "측정 시작하기" → POST /body/photos/(multipart) → 트랜잭션 폴링 →
 *              폴링 응답에 담겨 오는 추론 치수를 그대로 사용 (startPhotoMeasurement)
 *   - STEP3  "완료"  → PATCH /users/me/body/detail/  로 수정한 둘레를 저장 (saveDetail)
 * basic/detail 저장은 best-effort — 실패해도 로컬 상태로 플로우는 계속되고, 화면이 토스트로 알린다.
 */

export type Sex = 'female' | 'male' | 'none';

export type MeasureInput = { height: number; weight: number; sex: Sex };
/** 사진 URI (없으면 null). 지금은 실제 카메라 대신 mock URI 를 넣는다. */
export type MeasurePhotos = { front: string | null; side: string | null };

/**
 * 상세 치수 10개 — 둘레·너비 7개 + 체형 지표 3개(목길이·허벅지:종아리·상하체).
 * 키가 백엔드 필드명 그대로라 PATCH detail 본문에 통째로 넣을 수 있다.
 * 라벨·단위·허용 범위·'재는 법'은 constants/body-measures.ts 가 단일 출처다.
 */
export type Measurement = Record<BodyMeasureKey, number>;
export type SizeMatch = { brand: string; size: string; fit: string };

export type MeasureResult = {
  measures: Measurement;
  sizes: SizeMatch[];
  usedPhotos: boolean; // 사진을 써서 추정했는지 (안내문 분기용)
  photoFallback: boolean; // 사진 인식 실패 후 기본 정보만으로 추정했는지
  bodyType: string | null;
  bodyTypeLabel: string | null;
};

type EstimateStatus = 'idle' | 'loading' | 'success' | 'error';

type MeasureState = {
  input: MeasureInput | null;
  photos: MeasurePhotos;
  status: EstimateStatus;
  result: MeasureResult | null;
  error: string | null;
  photoQualityFailed: boolean;
  /* 실패 원인이 "추정할 기본 정보가 없다"인지. 화면이 안내를 갈라 쓴다
     (입력하러 보내기 vs 다시 시도). 로그인 만료·서버 장애를 "정보가 없어요"로
     보여주면 사용자가 엉뚱한 곳으로 간다. */
  needsInput: boolean;
};

const EMPTY: MeasureState = {
  input: null,
  photos: { front: null, side: null },
  status: 'idle',
  result: null,
  error: null,
  photoQualityFailed: false,
  needsInput: false,
};

let state: MeasureState = EMPTY;
const listeners = new Set<() => void>();

function setState(next: Partial<MeasureState>): void {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

/** TODO(backend): 브랜드 사이즈 매칭은 아직 API 가 없어 가슴둘레로 흉내 낸다. */
function mockSizes(chest: number): SizeMatch[] {
  const tier = chest < 90 ? 'S' : chest < 98 ? 'M' : 'L';
  const up = tier === 'S' ? 'M' : tier === 'M' ? 'L' : 'XL';
  return [
    { brand: '무신사 스탠다드', size: tier, fit: '딱 맞음' },
    { brand: '유니클로', size: up, fit: '여유 있음' },
    { brand: 'COS', size: tier, fit: '딱 맞음' },
  ];
}

// ── 백엔드 신체치수(GET /body/) ────────────────────────────────
// DRF DecimalField 는 문자열("170.0")로 내려올 수 있어 숫자로 정규화한다. 미입력은 null.
type Numeric = string | number | null;

type BodyDto = Record<BodyMeasureKey, Numeric> & {
  gender: string | null;
  height: Numeric;
  weight: Numeric;
  body_type: string | null;
  body_type_label: string | null;
  updated_at: string | null;
};

type BodyEstimationResult = {
  status: 'pending' | 'processing' | 'succeeded' | 'failed';
  measurement: BodyDto;
  error_message?: string | null;
  error_code?: string | null;
};

function toNum(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * GET /body/ 조회. 실패는 그대로 던진다.
 * 미입력 사용자도 200 에 전 필드 null 로 내려오므로(views.BodyMeasurementView),
 * 여기서 나는 예외는 전부 진짜 오류(세션 만료·오프라인·서버 장애)다.
 * 예전엔 이걸 삼켜 null 로 바꿨는데, 그러면 호출부가 "저장된 값 없음"과 구분하지 못해
 * 화면이 조용히 빈 칸·mock 값으로 넘어갔다.
 */
async function fetchBody(): Promise<BodyDto> {
  return api.get<BodyDto>(BodyEndpoints.me);
}

/**
 * 응답 치수 → 화면이 쓰는 10개. 하나라도 비면 null 을 돌려 호출부가 실패로 처리한다.
 * 서버는 추정에 성공하면 상세 10개를 모두 채워 주므로, 빈 칸은 "추정이 안 된 것"이다.
 * 예전엔 빈 칸을 키·몸무게 공식으로 만든 값으로 메웠는데, 그러면 추정에 실패해도
 * 그럴듯한 숫자가 결과로 앉아 사용자가 구분할 수 없었다.
 */
function toMeasurement(dto: BodyDto): Measurement | null {
  const measures = {} as Measurement;
  for (const spec of BODY_MEASURES) {
    const value = toNum(dto[spec.key]);
    if (value === null) return null;
    measures[spec.key] = value;
  }
  return measures;
}

function isMissingBasicInfo(error: unknown, input: MeasureInput | null): boolean {
  const sentBasicInfo = Boolean(input && input.sex !== 'none');
  return !sentBasicInfo && error instanceof ApiError && error.status === 400;
}

/** 추정 결과 → 스토어 결과. 치수가 덜 왔으면 null (호출부가 실패로 알린다). */
function toResult(
  outcome: BodyEstimationResult,
  usedPhotos: boolean,
  photoFallback = false,
): MeasureResult | null {
  const measures = toMeasurement(outcome.measurement);
  if (!measures) return null;
  return {
    measures,
    sizes: mockSizes(measures.chest),
    ...measurementResultSource(usedPhotos, photoFallback),
    bodyType: outcome.measurement.body_type,
    bodyTypeLabel: outcome.measurement.body_type_label,
  };
}

/**
 * STEP1 프리필용 — 저장된 성별·키·몸무게 (미입력이면 각각 null).
 * 조회 자체가 실패하면 던진다. 호출부가 "값이 없음"과 "못 불러옴"을 구분해 안내해야 한다.
 */
export async function fetchBodyBasic(): Promise<{
  sex: 'female' | 'male' | null;
  height: number | null;
  weight: number | null;
}> {
  const dto = await fetchBody();
  return {
    sex: dto.gender === 'female' || dto.gender === 'male' ? dto.gender : null,
    height: toNum(dto.height),
    weight: toNum(dto.weight),
  };
}

// ── 사진 기반 측정 (POST photos → 폴링) ─────────────────────────
/** POST /body/photos/ 접수 응답 (202). */
type PhotoTxResponse = { transaction_id: string; status: string };

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/* 폴링 상한은 서버가 포기하는 시점보다 넉넉해야 한다.
   서버 VLM 호출 타임아웃이 기본 120초이고 응답이 잘리면 한 번 더 부르므로 최악 240초인데,
   예전엔 60초에 끊었다 — 서버는 측정 중인데 화면만 "실패"로 뜨고, 그 뒤 성공한 값이
   조용히 저장돼 "실패했다면서 값은 바뀌어 있는" 상태가 됐다. */
const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 150; // 약 5분
/** 조회가 연속으로 이만큼 실패하면 포기 — 네트워크 순단 한 번에 측정 전체를 버리지 않는다. */
const POLL_MAX_CONSECUTIVE_ERRORS = 3;

/* 상한을 넘겨 화면만 먼저 포기한 트랜잭션. 서버는 사용자당 진행중 1건만 허용하므로
   다시 시도할 때 사진을 새로 올리면 400 만 받는다 — 올리지 말고 이어서 기다려야 한다. */
let pendingTransactionId: string | null = null;

/** FormData 파일 파트 추가 — 웹은 Blob, 네이티브는 {uri,name,type}. */
async function appendImage(
  form: FormData,
  field: string,
  uri: string,
  name: string,
): Promise<void> {
  if (Platform.OS === 'web') {
    const blob = await (await fetch(uri)).blob();
    form.append(field, blob, name);
  } else {
    // RN 네이티브 FormData 파일 파트 형식
    form.append(field, { uri, name, type: 'image/jpeg' } as unknown as Blob);
  }
}

/** 정면·측면 사진을 multipart 로 업로드 → 측정 트랜잭션 생성(202). */
async function uploadBodyPhotos(
  frontUri: string,
  sideUri: string,
  input: MeasureInput | null,
): Promise<PhotoTxResponse> {
  const form = new FormData();
  await appendImage(form, 'front_image', frontUri, 'front.jpg');
  await appendImage(form, 'side_image', sideUri, 'side.jpg');
  /* 기본 정보도 함께 보낸다(서버가 받아 준다). 생략하면 저장된 값을 쓰는데,
     STEP1 의 PUT basic 이 실패했을 때 저장된 값이 없어 여기서 400 이 난다 —
     "임시로 진행할게요" 라고 안내해 놓고 다음 단계에서 막히는 셈이었다. */
  if (input && input.sex !== 'none') {
    form.append('gender', input.sex);
    form.append('height', String(input.height));
    form.append('weight', String(input.weight));
  }

  if (Platform.OS === 'web') {
    return api.post<PhotoTxResponse>(BodyEndpoints.photos, form);
  }

  /* Expo의 전역 fetch는 네이티브 { uri, name, type } 파일 파트를 처리하지 못한다.
     옷장·룩북과 같은 XHR 업로더를 써야 두 사진이 실제 multipart로 전달된다. */
  const response = await uploadMultipart(`${API_BASE_URL}${BodyEndpoints.photos}`, form, {
    token: await getAccessToken(),
  });
  return parsePhotoUploadResponse(response);
}

function parsePhotoUploadResponse(response: { status: number; body: string }): PhotoTxResponse {
  let data: unknown = null;
  try {
    data = response.body ? JSON.parse(response.body) : null;
  } catch {
    data = response.body;
  }

  if (response.status < 200 || response.status >= 300) {
    const detail = (data as { detail?: string } | null)?.detail;
    throw new ApiError(
      detail ?? `사진 측정 요청에 실패했어요. (${response.status})`,
      response.status,
      data,
    );
  }
  return data as PhotoTxResponse;
}

/**
 * 측정 트랜잭션을 종료 상태(succeeded/failed)까지 폴링해 응답 전체를 돌려준다.
 * 실패 사유(error_message)와 추정 결과(measurement)가 이 응답에 다 들어 있어서,
 * 호출부가 사유를 그대로 보여주고 별도 GET 없이 치수를 쓸 수 있다.
 * 상한 안에 안 끝나면 null — 실패가 아니라 "서버에서 아직 진행 중"이라 안내 문구가 다르다.
 */
async function pollTransaction(transactionId: string): Promise<BodyEstimationResult | null> {
  let consecutiveErrors = 0;
  for (let i = 0; i < POLL_MAX_ATTEMPTS; i++) {
    try {
      const tx = await api.get<BodyEstimationResult>(BodyEndpoints.photo(transactionId));
      consecutiveErrors = 0;
      if (tx.status === 'succeeded' || tx.status === 'failed') return tx;
    } catch (e) {
      /* 4xx 는 기다린다고 풀리지 않는다(트랜잭션 없음·세션 만료) — 사유를 그대로 올린다.
         5xx·네트워크 순단은 다음 차례에 다시 물어본다. */
      if (e instanceof ApiError && e.status < 500) throw e;
      if (++consecutiveErrors >= POLL_MAX_CONSECUTIVE_ERRORS) throw e;
    }
    await delay(POLL_INTERVAL_MS);
  }
  return null;
}

export const measureStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getState(): MeasureState {
    return state;
  },

  /** 새 측정 플로우 시작 — 이전 데이터 초기화 (STEP1 진입 시 호출) */
  reset(): void {
    pendingTransactionId = null;
    setState({ ...EMPTY, photos: { front: null, side: null } });
  },

  setInput(input: MeasureInput): void {
    setState({ input });
  },

  /**
   * STEP1 "다음" — 키·몸무게를 서버에 저장(PUT basic)하고 로컬 입력도 반영한다.
   * 로컬 반영을 먼저 하므로 저장이 실패해도(오프라인 등) 플로우는 이어지고,
   * 실패는 throw 하여 화면이 토스트로 알리게 한다.
   */
  async saveBasic(input: MeasureInput): Promise<void> {
    setState({ input });
    /* 서버는 gender·height·weight 를 **셋 다** 요구하고, gender 는 male|female 만 받는다.
       예전엔 gender 를 안 보내 매번 400 이 났다.
       성별을 안 고른 상태로는 저장할 방법이 없으므로 요청을 보내지 않는다 —
       어차피 400 이 될 요청을 던져 에러 토스트만 띄우느니 로컬 입력만 들고 진행한다.
       (화면에서 성별을 고르게 막아 두어 여기까진 잘 오지 않는다) */
    if (input.sex === 'none') return;
    await api.put(BodyEndpoints.basic, {
      gender: input.sex,
      height: input.height,
      weight: input.weight,
    });
  },

  setPhoto(key: keyof MeasurePhotos, uri: string): void {
    setState({ photos: { ...state.photos, [key]: uri } });
  },

  /**
   * 사진 없이 치수 추정 — POST /body/estimate/ (서버가 학습 모델로 상세 10개를 채우고 저장한다.
   * 둘레 7개는 회귀 모델, 목길이·허벅지:종아리는 성별 회귀식, 상하체 비율은 기준값 0.786).
   * STEP2 "사진 없이 진행할게요" 와 결과 화면 직접 진입에서 호출하고, 결과는 STEP3 가 구독한다.
   * 화면이 언마운트돼도 이 스토어에 결과가 남으므로, 나갔다 돌아와도 결과가 유지된다.
   */
  async estimate(photoFallback = false): Promise<void> {
    setState({
      status: 'loading',
      error: null,
      result: null,
      needsInput: false,
      photoQualityFailed: false,
    });
    try {
      /* 이번 플로우에서 받은 입력이 있으면 그 값으로 추정한다(저장도 함께 된다).
         없으면 본문을 비워 서버가 저장해 둔 기본 정보를 쓰게 한다 —
         그것마저 없으면 400 으로 "성별·키·몸무게가 필요하다"는 사유가 내려온다.
         예전엔 여기서 170/63 기본값으로 계산해, 사용자가 준 적 없는 수치를 결과로 보여줬다. */
      const input = state.input;
      const body =
        input && input.sex !== 'none'
          ? { gender: input.sex, height: input.height, weight: input.weight }
          : {};
      const outcome = await api.post<BodyEstimationResult>(BodyEndpoints.estimate, body);

      /* 사진을 쓰지 않는 경로다. 촬영까지 갔다가 실패해 되돌아오면 photos 는 남아 있는데,
         그걸 보고 usedPhotos 를 켜면 사진으로 잰 적 없는 값이 "사진 기반 결과"로 표시된다. */
      const result = toResult(outcome, false, photoFallback);
      if (!result) {
        setState({
          status: 'error',
          error: '치수를 받지 못했어요. 다시 시도해주세요.',
          needsInput: false,
          ...measurementRequestFailureState(),
        });
        return;
      }
      setState({ status: 'success', result });
    } catch (e) {
      /* 이 엔드포인트의 400 은 둘 중 하나다 — 추정할 기본 정보가 없거나, 값이 허용 범위 밖.
         서버 문구에는 "PUT /api/v1/users/me/body/basic/ 으로…" 같은 개발자용 안내가 섞여 있어
         그대로 보여주지 않는다. */
      const needsInput = e instanceof ApiError && e.status === 400;
      setState({
        status: 'error',
        needsInput,
        ...measurementRequestFailureState(),
        error: needsInput
          ? '키·몸무게와 성별을 확인해주세요. 입력이 없거나 범위를 벗어났어요.'
          : e instanceof ApiError
            ? e.message
            : '치수 추정에 실패했어요.',
      });
    }
  },

  /**
   * STEP2 "측정 시작하기" — 정면·측면 사진 업로드 → 측정 트랜잭션 폴링 →
   * 성공하면 폴링 응답에 담겨 온 상세치수를 그대로 쓴다.
   * 실패·지연은 원인을 구분해 알린다 (서버 사유 그대로 / 아직 진행 중).
   */
  async startPhotoMeasurement(): Promise<void> {
    const { front, side } = state.photos;
    if (!front || !side) {
      setState({
        status: 'error',
        result: null,
        error: '정면·측면 사진이 모두 필요해요.',
        needsInput: true,
        photoQualityFailed: false,
      });
      return;
    }
    setState({
      status: 'loading',
      error: null,
      result: null,
      needsInput: false,
      photoQualityFailed: false,
    });
    try {
      // 앞선 시도가 상한만 넘긴 거라면 그 트랜잭션을 이어서 기다린다.
      const transactionId =
        pendingTransactionId ?? (await uploadBodyPhotos(front, side, state.input)).transaction_id;
      pendingTransactionId = transactionId;

      const outcome = await pollTransaction(transactionId);
      if (!outcome) {
        // 서버는 아직 측정 중이다(10분까지 유지) — 실패로 단정하지 않는다.
        setState({
          status: 'error',
          error: '측정이 아직 끝나지 않았어요. 잠시 후 다시 시도해주세요.',
          photoQualityFailed: false,
        });
        return;
      }
      pendingTransactionId = null;
      if (outcome.status !== 'succeeded') {
        // 서버가 실패 사유를 error_message 로 준다. 고정 문구로 덮으면 원인을 앱에서 알 길이 없다.
        setState({
          status: 'error',
          error: outcome.error_message ?? '사진 측정에 실패했어요. 다시 시도해주세요.',
          ...photoMeasurementFailureState(outcome.error_code),
        });
        return;
      }
      /* 추론된 상세치수는 조회 응답에 함께 온다 — 따로 GET /body/ 를 부르지 않는다.
         그 GET 이 실패하면 mock 값이 "사진으로 측정한 결과"로 둔갑했었다. */
      const result = toResult(outcome, true);
      if (!result) {
        setState({
          status: 'error',
          error: '치수를 받지 못했어요. 다시 시도해주세요.',
          needsInput: false,
          photoQualityFailed: false,
        });
        return;
      }
      setState({ status: 'success', result });
    } catch (e) {
      // 이어서 기다릴 수 없는 상태(트랜잭션 없음·세션 만료 등)이므로 다음 시도는 새로 올린다.
      pendingTransactionId = null;
      setState({
        status: 'error',
        result: null,
        error:
          e instanceof ApiError
            ? e.message
            : '사진 측정에 실패했어요. 다시 시도해주세요.',
        // 업로드 400 도 기본 정보 부족이 원인일 수 있다 (서버가 저장된 값을 못 찾은 경우).
        needsInput: isMissingBasicInfo(e, state.input),
        photoQualityFailed: false,
      });
    }
  },

  /** STEP3 에서 사용자가 직접 수정한 치수를 반영 (로컬만) */
  updateMeasures(measures: Measurement): void {
    if (!state.result) return;
    setState({ result: { ...state.result, measures } });
  },

  /**
   * STEP3 "완료" — 수정한 상세 10개를 서버에 저장(PATCH detail)한다.
   * Measurement 의 키가 곧 API 필드명이라 그대로 본문이 된다.
   * 로컬 반영을 먼저 하므로 저장 실패해도 결과는 유지되고, 실패는 throw 로 알린다.
   */
  async saveDetail(measures: Measurement): Promise<void> {
    if (state.result) setState({ result: { ...state.result, measures } });
    /* 비율처럼 **서버가 계산하는 값은 되돌려 보내지 않는다.**
       보내면 서버가 가진 길이 값과 어긋난 비율이 저장됐다가 다음 추정에서 덮어써진다.
       무엇을 보낼지는 constants/body-measures.ts 의 editable 이 단일 출처다. */
    const body = Object.fromEntries(
      EDITABLE_MEASURES.map((spec) => [spec.key, measures[spec.key]]),
    );
    await api.patch(BodyEndpoints.detail, body);
  },
};

export function useMeasure(): MeasureState {
  return useSyncExternalStore(
    measureStore.subscribe,
    measureStore.getState,
    measureStore.getState,
  );
}
