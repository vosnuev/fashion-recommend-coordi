import { Platform } from 'react-native';

import { API_BASE_URL, ChatEndpoints } from '@/constants/config';
import { api, ApiError } from '@/lib/apiClient';
import { getAccessToken } from '@/lib/secureStore';
import {
  guessFileName,
  guessMimeType,
  isRemote,
  toLocalFile,
  uploadMultipart,
} from '@/lib/uploadFile';

/**
 * 채팅 API 의 원형(DTO)과 호출 함수.
 *
 * 여기서는 백엔드가 주는 모양을 **그대로** 둔다. 앱 화면이 쓰는 모양(말풍선 등)으로
 * 바꾸는 일은 state/chat.ts 가 맡는다 — 계약이 바뀌었을 때 고칠 자리를 한 곳으로 모으려는 것.
 */

/** 추천 방식. 앱의 'closet'/'taste' 와 1:1 대응한다 (state/chat.ts 의 toApiMode). */
export type ApiChatMode = 'WARDROBE_BASED' | 'NEW_ITEM';

export type ApiMessageRole = 'USER' | 'ASSISTANT' | 'SYSTEM' | 'TOOL';
export type ApiMessageStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

/**
 * run 의 상태. **SSE 이벤트 이름과 철자가 다르다** —
 * 이벤트 `completed` 가 여기서는 `SUCCEEDED` 다. 둘을 섞어 비교하지 말 것.
 */
export type ApiRunStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'NEEDS_CLARIFICATION'
  | 'SUCCEEDED'
  | 'FAILED';

/** 사진 무드 분석의 진행 상태. 승인/거절은 `SUCCEEDED` 가 된 뒤에만 할 수 있다. */
export type ApiAnalysisStatus =
  | 'NOT_REQUESTED'
  | 'QUEUED'
  | 'PROCESSING'
  | 'SUCCEEDED'
  | 'FAILED';

/** 무드를 추천 조건에 반영할지 정한 결과. 한 번 정하면 되돌릴 수 없다(서버가 409). */
export type ApiMoodDecision = 'UNDECIDED' | 'APPROVED' | 'REJECTED';

/** 승인/거절을 보낼 때 쓰는 값. 저장되는 값(APPROVED/REJECTED)과 **철자가 다르다**. */
export type ApiMoodDecisionInput = 'APPROVE' | 'REJECT';

/**
 * 사진에서 읽어낸 무드.
 * `tags` 는 사람에게 보여줄 짧은 한국어 단어이고, `styles`/`colors`/`fits` 는 추천 필터에
 * 그대로 들어가는 서비스 표준값이다. 화면에는 tags 만 쓴다.
 */
export type ApiMoodAnalysis = {
  summary: string;
  tags: string[];
  styles: string[];
  colors: string[];
  fits: string[];
};

export type ApiChatAttachment = {
  id: string;
  mime_type: string;
  size: number;
  analysis_status: ApiAnalysisStatus;
  /** 분석 전에는 빈 객체다. SUCCEEDED 일 때만 ApiMoodAnalysis 모양이 된다. */
  analysis_result: Partial<ApiMoodAnalysis> | null;
  mood_decision: ApiMoodDecision | null;
  image_url: string | null;
  created_at: string;
};

/**
 * 이 질문이 참고한 개인·공유 옷. 서버가 보여주기용으로만 추린 값이다.
 *
 * `image_url` 은 **조회할 때마다 새로 서명된다** — 저장해 두면 만료된다.
 * 서명에 실패하면 null 로 오고 나머지는 그대로 온다(이미지만 빠지고 말풍선은 살아난다).
 */
type ApiReferenceSummaryBase = {
  schema_version: string;
  item_name: string;
  category_large: string;
  owner_name: string;
  room_name: string;
  image_url: string | null;
};

export type ApiReferenceSummary = ApiReferenceSummaryBase &
  (
    | { type: 'SHARED_WARDROBE_ITEM'; shared_item_id: string }
    | { type: 'WARDROBE_ITEM'; wardrobe_item_id: string }
  );

export type ApiChatMessage = {
  id: string;
  sequence: number;
  role: ApiMessageRole;
  content: string;
  status: ApiMessageStatus;
  client_message_id: string;
  /** 추천이 붙은 답변이면 recommendation_result_id 가 여기 들어온다. */
  metadata: Record<string, unknown>;
  attachments: ApiChatAttachment[];
  /** 공유 옷을 참고한 질문에만 붙는다. 사용자 메시지가 아니면 null. */
  reference_summary?: ApiReferenceSummary | null;
  created_at: string;
  updated_at: string;
};

export type ApiChatSession = {
  id: string;
  mode: ApiChatMode;
  title: string;
  conversation_summary: string;
  last_message_at: string;
  created_at: string;
  updated_at: string;

  /* ── 스타일리스트 모드 ──
     ⚠️ 셋 다 **없을 수 있다.** 이 필드들은 origin/feature/chat-main-integration 에서 붙는데
        배포 서버·main 은 아직 그 전이라 아예 내려오지 않는다. 없을 때 DEFAULT 로 덮어쓰면
        방금 켠 모드가 목록 새로고침 한 번에 꺼지므로, 받는 쪽에서 '없음'과 'DEFAULT' 를
        구분해야 한다 (state/chat.ts 의 toSession). */
  response_mode?: 'DEFAULT' | 'STYLIST';
  selected_persona_ids?: string[];
  persona_selection_updated_at?: string | null;
};

export type ApiGuestIdentity = {
  identity_id: string;
  expires_at: string;
};

export type ApiChatRun = {
  id: string;
  session_id: string;
  request_message_id: string;
  response_message_id: string | null;
  status: ApiRunStatus;
  error_code: string;
  error_message: string;
  created_at: string;
  updated_at: string;
  wardrobe_scope_snapshot?: {
    system_categories: string[];
    hashtags: { id: string; name: string; position: number }[];
    match_mode: 'REQUIRED' | 'PREFERRED';
    candidate_item_ids: string[];
  };
};

export type ApiWardrobeScope = {
  system_categories?: string[];
  hashtag_ids?: string[];
  match_mode?: 'REQUIRED' | 'PREFERRED';
};

export type ApiMessageOptions = {
  wardrobeScope?: ApiWardrobeScope;
  reference?: ApiItemReference;
};

/** 메시지 전송의 202 응답. events_url 은 신뢰하지 않는다(config.ts 주석 참고). */
export type ApiMessageSubmit = {
  message: ApiChatMessage;
  run: ApiChatRun;
  events_url: string;
};

/**
 * 재전송을 서버가 같은 메시지로 알아보게 하는 값.
 * 같은 요청을 재시도할 때는 **같은 값**을 그대로 다시 보내야 중복 말풍선이 생기지 않는다.
 * (그래서 전송 함수가 내부에서 만들지 않고 호출자가 들고 있게 한다.)
 *
 * ⚠️ `run:` 으로 시작하면 서버가 400 을 낸다 — 서버가 답변 메시지에 쓰는 예약 접두사다.
 */
export function newClientMessageId(): string {
  return `c${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function listSessions(): Promise<ApiChatSession[]> {
  return api.get<ApiChatSession[]>(ChatEndpoints.sessions);
}

export function ensureGuestIdentity(): Promise<ApiGuestIdentity> {
  return api.post<ApiGuestIdentity>(ChatEndpoints.guest, undefined, { auth: false });
}

export async function createSession(
  mode: ApiChatMode,
  title?: string,
  options: { asGuest?: boolean } = {},
): Promise<ApiChatSession> {
  const asGuest = options.asGuest === true || !(await getAccessToken());
  if (asGuest) {
    await ensureGuestIdentity();
  }
  return api.post<ApiChatSession>(
    ChatEndpoints.sessions,
    { mode, ...(title ? { title } : {}) },
    // 화면이 비회원으로 확정한 요청에는 저장소에 남은 오래된 토큰을 절대 섞지 않는다.
    // 게스트 신원은 바로 위에서 받은 HttpOnly 쿠키로 전달된다.
    { auth: !asGuest },
  );
}

export function renameSession(sessionId: string, title: string): Promise<ApiChatSession> {
  return api.patch<ApiChatSession>(ChatEndpoints.session(sessionId), { title });
}

export function deleteSession(sessionId: string): Promise<void> {
  return api.delete<void>(ChatEndpoints.session(sessionId));
}

/* 전체 메시지를 한 번에 주는 GET /messages/ 도 서버에 있지만 쓰지 않는다 —
   대화가 길수록 열 때마다 느려지고, 추천이 붙은 메시지는 카드 조회까지 그만큼 늘어난다. */

/** 커서 페이지 공통 꼬리. `next_cursor` 는 더 받을 게 있을 때만 채워진다. */
type ApiCursorPage = {
  total_count: number;
  next_cursor: string | null;
  has_more: boolean;
};

export type ApiMessagePage = ApiCursorPage & {
  /** **시간순**이다(최신이 뒤). 첫 페이지가 가장 최근 묶음이고 커서로 과거를 더 받는다. */
  items: ApiChatMessage[];
};

/**
 * 최신 메시지부터 끊어 받는다.
 * 첫 요청은 cursor 없이, 다음부터는 직전 응답의 next_cursor 를 그대로 넘긴다.
 * 커서는 서명된 값이라 손대면 400 이 난다 — 만들지 말고 받은 것만 쓸 것.
 */
export function pageMessages(
  sessionId: string,
  options: { limit?: number; cursor?: string } = {},
): Promise<ApiMessagePage> {
  const query = new URLSearchParams();
  if (options.limit) query.set('limit', String(options.limit));
  if (options.cursor) query.set('cursor', options.cursor);
  const suffix = query.toString();
  return api.get<ApiMessagePage>(
    `${ChatEndpoints.messagePage(sessionId)}${suffix ? `?${suffix}` : ''}`,
  );
}

/** 검색어가 걸린 메시지 미리보기. 제목만 걸린 세션은 null 이다. */
export type ApiSessionSearchMatch = {
  message_id: string;
  sequence: number;
  role: ApiMessageRole;
  preview: string;
};

export type ApiChatSessionSearchItem = ApiChatSession & {
  search_match: ApiSessionSearchMatch | null;
};

export type ApiSessionSearchPage = ApiCursorPage & {
  /** 서버가 공백을 정리한 검색어. 응답이 어떤 질의의 것인지 확인하는 데 쓴다. */
  query: string;
  items: ApiChatSessionSearchItem[];
};

/**
 * 세션 제목과 **저장된 메시지 본문**을 서버에서 찾는다.
 * 앱이 받아둔 대화만 훑던 지역 검색과 달리, 한 번도 열지 않은 대화도 걸린다.
 *
 * ⚠️ 검색어가 바뀌면 cursor 를 버리고 첫 페이지부터 다시 받아야 한다 —
 *    서버가 커서에 검색어를 함께 서명해 두고 다르면 400 을 낸다.
 */
export function searchSessions(
  query: string,
  options: { limit?: number; cursor?: string } = {},
): Promise<ApiSessionSearchPage> {
  const params = new URLSearchParams({ query });
  if (options.limit) params.set('limit', String(options.limit));
  if (options.cursor) params.set('cursor', options.cursor);
  return api.get<ApiSessionSearchPage>(`${ChatEndpoints.sessionSearch}?${params}`);
}

/**
 * 개인·공유 옷장 아이템 한 벌을 **참고 이미지**로 지목하는 값.
 *
 * type에 따라 `shared_item_id` 또는 `wardrobe_item_id` 하나만 보낸다.
 * ⚠️ 한 번에 **한 벌만** 보낼 수 있다.
 * ⚠️ 이 값은 write-only 다. 메시지를 다시 받아와도 무엇을 참고했는지 알 수 없다
 *    (서버는 ChatRun.reference_snapshot 에 두는데 아직 어떤 응답에도 안 나온다).
 */
export type ApiItemReference =
  | { type: 'SHARED_WARDROBE_ITEM'; shared_item_id: string }
  | { type: 'WARDROBE_ITEM'; wardrobe_item_id: string };

/**
 * 옷장 아이템 참고가 실패한 이유. 서버가 `{ code, detail }` 로 준다
 * (api/apps/chat/services/shared_reference.py).
 *
 * **문자열이 아니라 이 코드로 갈라야 한다** — 안내 문구가 바뀌어도 분기가 안 깨진다.
 */
export type ApiReferenceErrorCode =
  | 'REFERENCE_ITEM_NOT_FOUND'
  | 'REFERENCE_ITEM_FORBIDDEN'
  | 'REFERENCE_ITEM_NOT_READY'
  | 'REFERENCE_ITEM_INVALID';

/** 응답 본문에서 참고 실패 코드를 꺼낸다. 다른 오류면 null. */
export function referenceErrorCode(error: unknown): ApiReferenceErrorCode | null {
  if (!(error instanceof ApiError)) return null;
  const code = (error.data as { code?: string } | null)?.code;
  return code === 'REFERENCE_ITEM_NOT_FOUND' ||
    code === 'REFERENCE_ITEM_FORBIDDEN' ||
    code === 'REFERENCE_ITEM_NOT_READY' ||
    code === 'REFERENCE_ITEM_INVALID'
    ? code
    : null;
}

/**
 * 질문 전송. **답변은 이 응답에 들어있지 않다** — 202 로 접수만 되고,
 * 실제 답변은 run 을 구독해야 온다(lib/chatStream.ts).
 *
 * `reference` 를 함께 보내면 서버가 그 옷장 아이템을 **참고 이미지**로 삼아,
 * 비슷한 내 옷(옷장 기반)이나 비슷한 상품(추구미 반영)을 찾는다.
 * 친구 옷 자체는 최종 코디에 들어가지 않는다.
 */
export function sendMessage(
  sessionId: string,
  content: string,
  clientMessageId: string,
  options: ApiMessageOptions = {},
): Promise<ApiMessageSubmit> {
  return api.post<ApiMessageSubmit>(ChatEndpoints.messages(sessionId), {
    content,
    client_message_id: clientMessageId,
    ...(options.wardrobeScope ? { wardrobe_scope: options.wardrobeScope } : {}),
    ...(options.reference ? { reference: options.reference } : {}),
  });
}

/** run 단건 조회 — 네이티브 폴링과 SSE 실패 시 복구에 쓴다. */
export function getRun(runId: string): Promise<ApiChatRun> {
  return api.get<ApiChatRun>(ChatEndpoints.run(runId));
}

/* ── 사진 첨부 ─────────────────────────────────────── */

export type ApiAttachmentUpload = {
  message: ApiChatMessage;
  attachment: ApiChatAttachment;
  /** false 면 같은 client_message_id 로 이미 올린 사진이다 (재시도가 중복을 만들지 않는다). */
  created: boolean;
};

export type ApiMoodAnalysisStart = {
  attachment: ApiChatAttachment;
  run: ApiChatRun;
  events_url: string;
};

export type ApiMoodDecisionResult = {
  attachment: ApiChatAttachment;
  changed: boolean;
  /** 이 무드가 실제로 추천 조건에 반영됐는지 */
  applied: boolean;
};

/**
 * 사진 올리기. 사진만 저장되고 **분석은 아직 시작되지 않는다** — startMoodAnalysis 로 따로 건다.
 *
 * 업로드 방식이 플랫폼마다 다르다. 옷장·룩북과 같은 이유이고 같은 방식을 쓴다.
 *   - 웹: 고른 사진을 Blob 으로 만들어 표준 FormData 에 넣는다.
 *   - 네이티브: Expo SDK 54+ 의 전역 fetch 가 표준(WinterCG) 구현이라 예전 RN 관용구인
 *     `{ uri, name, type }` 파트를 받지 못한다(`Unsupported FormDataPart implementation`).
 *     XHR 은 이 파트를 네이티브로 처리하므로 uploadMultipart 로 보낸다.
 *
 * ⚠️ 네이티브 경로는 apiClient 를 타지 않아 401 자동 재발급이 없다 — 토큰을 직접 붙인다.
 */
export async function uploadAttachment(
  sessionId: string,
  input: { uri: string; name?: string; mimeType?: string },
  clientMessageId: string,
  content = '',
): Promise<ApiAttachmentUpload> {
  const path = ChatEndpoints.attachments(sessionId);
  const name = input.name ?? guessFileName(input.uri, 'chat-photo.jpg');
  const mimeType = input.mimeType ?? guessMimeType(name);
  const form = new FormData();

  if (Platform.OS === 'web') {
    const blob = await fetch(input.uri).then((r) => {
      if (!r.ok) throw new Error('선택한 사진을 불러오지 못했어요.');
      return r.blob();
    });
    form.append('image', blob, name);
    form.append('client_message_id', clientMessageId);
    if (content) form.append('content', content);
    return api.post<ApiAttachmentUpload>(path, form);
  }

  const local = isRemote(input.uri) ? await toLocalFile(input.uri, name) : null;
  const uri = local ? local.file.uri : input.uri;
  try {
    form.append('image', { uri, name, type: mimeType } as unknown as Blob);
    form.append('client_message_id', clientMessageId);
    if (content) form.append('content', content);
    const token = await getAccessToken();
    const res = await uploadMultipart(`${API_BASE_URL}${path}`, form, { token });
    if (res.status >= 400) {
      throw new ApiError(uploadErrorMessage(res.body), res.status, res.body);
    }
    return JSON.parse(res.body) as ApiAttachmentUpload;
  } finally {
    // 내려받은 임시 파일만 지운다. 사용자가 고른 사진은 우리 것이 아니다.
    if (local?.downloaded) {
      try {
        local.file.delete();
      } catch {
        // 캐시 파일이라 못 지워도 그냥 둔다.
      }
    }
  }
}

/** XHR 응답 본문에서 서버가 준 사유를 꺼낸다. JSON 이 아니면 일반 문구로 돌아간다. */
function uploadErrorMessage(body: string): string {
  try {
    const data = JSON.parse(body) as { detail?: string; image?: string[] };
    return data.detail ?? data.image?.[0] ?? '사진을 올리지 못했어요';
  } catch {
    return '사진을 올리지 못했어요';
  }
}

/** 무드 분석 시작. 답변과 같은 run 구조라 끝날 때까지 기다려야 한다. */
export function startMoodAnalysis(
  sessionId: string,
  attachmentId: string,
): Promise<ApiMoodAnalysisStart> {
  return api.post<ApiMoodAnalysisStart>(
    ChatEndpoints.attachmentAnalysis(sessionId, attachmentId),
  );
}

/** 읽어낸 무드를 추천 조건에 반영할지 결정한다. 보내는 값은 APPROVE/REJECT 다. */
export function decideMood(
  sessionId: string,
  attachmentId: string,
  decision: 'APPROVE' | 'REJECT',
): Promise<ApiMoodDecisionResult> {
  return api.post<ApiMoodDecisionResult>(
    ChatEndpoints.attachmentMoodDecision(sessionId, attachmentId),
    { decision },
  );
}
