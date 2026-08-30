import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { Editorial } from '@/constants/theme';
import {
  createSession as apiCreateSession,
  decideMood as apiDecideMood,
  deleteSession as apiDeleteSession,
  listSessions as apiListSessions,
  newClientMessageId,
  pageMessages as apiPageMessages,
  renameSession as apiRenameSession,
  searchSessions as apiSearchSessions,
  sendMessage as apiSendMessage,
  startMoodAnalysis as apiStartMoodAnalysis,
  uploadAttachment as apiUploadAttachment,
  type ApiChatMessage,
  type ApiChatMode,
  type ApiChatRun,
  type ApiChatSession,
  type ApiChatSessionSearchItem,
  type ApiWardrobeScope,
  type ApiMoodDecision,
  type ApiMoodDecisionInput,
  type ApiMoodAnalysis,
  referenceErrorCode,
  type ApiReferenceErrorCode,
} from '@/lib/chatApi';
import { isAnswered, waitForRun, waitForStylistRun } from '@/lib/chatStream';
import {
  getRecommendationResult,
  itemImageUrl,
  type ApiRecommendationCard,
  type ApiReferenceMatch,
  type ApiRenderJob,
  type ApiRenderStatus,
} from '@/lib/recommendApi';
import { recommendationCategoryTags } from '@/lib/recommendationPresentation';
import {
  buildReferenceBadge,
  buildReferenceBubble,
} from '@/lib/sharedReferencePresentation';
import {
  getStylistRun,
  getCardRenderStatus as apiGetCardRenderStatus,
  requestAlternative as apiRequestAlternative,
  renderCard as apiRenderCard,
  retryPersona as apiRetryPersona,
  saveCard as apiSaveCard,
  updateResponseMode as apiUpdateResponseMode,
  type ApiPersonaResult,
  type ApiResponseMode,
  type ApiStylistRun,
  type StylistId,
} from '@/lib/stylistApi';
import { stylistStore } from '@/state/stylist';
import { savedLookStore } from '@/state/saved';

/**
 * 채팅 세션 — 목록(C1)·대화(C2)·모드 선택(C3)이 같은 출처를 봐야 하므로 여기로 모았다.
 *
 * 서버(/api/v1/chat/*)가 원본이고 이 스토어는 그 사본이다. 화면은 서버 모양을 몰라도 되게
 * 여기서 앱 모양(말풍선·모드 이름)으로 옮긴다.
 *
 * ⚠️ **답변은 동기로 오지 않는다.** 질문을 보내면 서버는 202 로 접수만 하고 run 을 만든다.
 *    답변이 생길 때까지 기다리는 일은 lib/chatStream.ts 가 맡는다.
 * 회원은 JWT, 게스트는 HttpOnly 쿠키 신원으로 같은 채팅 API를 사용한다.
 */

/** 추천 방식. chat-mode 화면의 두 카드와 1:1 대응한다. */
export type ChatMode = 'taste' | 'closet';

/** 모드의 이름·색. 목록의 그룹 머리와 대화 헤더 배지가 같은 값을 쓴다. */
export const CHAT_MODE_META: Record<ChatMode, { label: string; tint: string }> = {
  taste: { label: '추구미 반영', tint: Editorial.wine },
  closet: { label: '옷장 기반', tint: Editorial.ink },
};

/** 목록에 그릴 순서 — Object.keys 는 순서를 보장하는 것처럼 읽히지 않으므로 명시한다. */
export const CHAT_MODE_ORDER: ChatMode[] = ['taste', 'closet'];

/* ── 서버 ↔ 앱 모드 이름 옮기기 ──
   'closet'(옷장 기반)은 내 옷만 쓰고, 'taste'(추구미 반영)는 새 상품까지 포함한다. */
export function toApiMode(mode: ChatMode): ApiChatMode {
  return mode === 'closet' ? 'WARDROBE_BASED' : 'NEW_ITEM';
}

function fromApiMode(mode: ApiChatMode): ChatMode {
  return mode === 'WARDROBE_BASED' ? 'closet' : 'taste';
}

/**
 * 한 개의 말풍선.
 * 타이핑 표시(···)는 저장하지 않는다 — 답변을 기다리는 '지금'만의 상태라
 * 대화를 다시 열었을 때 남아 있으면 안 된다. 화면 쪽 지역 상태로 둔다.
 */
/** 추천 코디 한 벌을 이루는 아이템. */
export type RecItem = {
  id: string;
  name: string;
  category: string | null;
  /** 걸 수 있는 주소일 때만 채운다 (S3 키는 걸러진다 — lib/recommendApi 의 itemImageUrl). */
  imageUrl: string | null;
  /** 새로 사야 하는 상품만 가격이 있다. 옷장에 있는 옷은 null. */
  price: number | null;
  fromWardrobe: boolean;
};

export type ChatMessage =
  | { id: string; role: 'ai' | 'user'; kind: 'text'; text: string }
  /** 사용자가 올린 사진. uri 가 없던 시절(목업)에도 말풍선은 떠서 optional 로 둔다. */
  | { id: string; role: 'user'; kind: 'image'; uri?: string }
  /**
   * 첨부한 사진에서 읽어낸 무드 — 추구미로 삼을지 묻는 카드.
   * `decision` 이 UNDECIDED 일 때만 고를 수 있다. 서버가 첫 결정만 받으므로 번복은 없다.
   */
  | {
      id: string;
      role: 'ai';
      kind: 'mood';
      /** 결정을 보낼 때 필요하다. 카드가 어느 사진에서 나왔는지도 이 값으로 안다. */
      attachmentId: string;
      tags: string[];
      summary: string;
      /** null 이면 아직 안 고른 상태 — 그때만 버튼을 보여준다. */
      decision: 'APPROVED' | 'REJECTED' | null;
      //decision: ApiMoodDecision;
    }
  /**
   * 참고할 개인·공유 옷을 함께 보낸 질문.
   *
   * 친구 옷은 **참고 대상이지 최종 코디 아이템이 아니다** — 문구에 '포함'을 쓰지 않는다.
   *
   * 서버가 메시지에 `reference_summary` 로 돌려주므로 대화를 다시 열어도 그대로 복원된다.
   * 보내는 순간에만 앱이 임시로 만들고(기다리는 동안), 곧 서버판으로 갈아 끼워진다.
   */
  | {
      id: string;
      role: 'user';
      kind: 'reference';
      text: string;
      referenceType: 'SHARED_WARDROBE_ITEM' | 'WARDROBE_ITEM';
      referenceItemId: string;
      imageUrl: string | null;
      itemName: string;
      ownerName: string;
      roomName?: string;
    }
  /**
   * 답변을 못 받은 질문 아래에 남기는 줄.
   * 토스트는 사라지므로, 대화를 다시 열었을 때 "질문만 있고 답이 없는" 상태로 보이지 않게 한다.
   */
  | {
      id: string;
      role: 'ai';
      kind: 'error';
      text: string;
      action?: 'OPEN_WARDROBE';
    }
  /** 추천 코디 카드. 답변 말풍선 뒤에 붙는다. */
  | {
      id: string;
      role: 'ai';
      kind: 'rec';
      /** 카드 상세·피드백·이미지 API 를 부를 때 쓰는 두 값 (/rec-card 로 넘긴다). */
      resultId: string;
      cardId: string;
      title: string;
      tags: string[];
      items: RecItem[];
      /** 새로 사야 하는 상품 합계. 옷장 옷만으로 짠 코디면 0 이라 표시하지 않는다. */
      totalPrice: number | null;
      warnings: string[];
      /** 카드 아래에서만 보여주는 룩 전체 추천 이유. */
      rationale: string;
      /** 공유 옷을 참고한 추천일 때만. 아니면 null 이라 배지를 안 그린다. */
      referenceBadge: ReferenceBadge | null;
    }
  /**
   * 응답 모드가 바뀐 자리에 남기는 줄. **말풍선이 아니다** — 오간 말이 아니라 상태 표시라,
   * 실패 줄과 같은 결로 그린다. 여기부터 답하는 방식이 달라졌음을 되돌아봤을 때 알 수 있게 한다.
   */
  | {
      id: string;
      role: 'ai';
      kind: 'mode';
      mode: ApiResponseMode;
      /** STYLIST 일 때 답할 사람들의 이름 */
      names: string[];
    }
  /** 스타일리스트별 카드 묶음. 인원수만큼 자리가 먼저 생기고 끝난 것부터 채워진다. */
  | {
      id: string;
      role: 'ai';
      kind: 'stylist';
      runId: string;
      cards: StylistCard[];
    };

/**
 * 스타일리스트 한 명이 내놓은 카드의 화면용 모양.
 * 아직 안 끝났으면 status 가 PENDING/RUNNING 이고 items 는 비어 있다 — 그 상태로도 자리는 있다.
 */
export type StylistCard = {
  personaId: StylistId;
  name: string;
  /** 카드 순서를 고정하는 값. 끝난 순서로 자리가 바뀌면 볼 때마다 위치가 달라진다. */
  order: number;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  /** 페르소나 관점의 핵심 문장 하나 */
  message: string;
  /** 접힌 영역에 보여줄 근거 코드 (state/stylist.ts 의 reasonLabel 로 옮겨 그린다) */
  reasonCodes: string[];
  items: RecItem[];
  totalPrice: number | null;
  warnings: string[];
  /** '이 코디로 할래요'·'다른 추천'에 필요하다. 아직 결과가 없으면 null. */
  resultId: string | null;
  cardId: string | null;
  errorCode: string | null;
  errorText: string | null;
  /** 다른 추천을 받는 중. 지금 카드는 남겨 두고 표시만 바꾼다. */
  alternating: boolean;
  alternativeCount: number;
  saved: boolean;
  /** 선택한 카드에만 생기는 코디 이미지 작업 상태. */
  renderStatus: ApiRenderStatus | null;
  renderImageUrl: string | null;
  renderErrorText: string | null;
  /** 공유 옷 참고 배지. 서버가 아직 이 필드를 안 줘서 대개 null 이다(lib/stylistApi.ts 주석). */
  referenceBadge: ReferenceBadge | null;
};

export type ChatSession = {
  id: string;
  mode: ChatMode;
  title: string;
  /** 서버가 가진 대화의 사본. 새로고침하면 이 배열은 통째로 다시 만들어진다. */
  messages: ChatMessage[];
  /**
   * 화면에 그릴 순서 — messages 에 스타일리스트 카드·모드 구분선을 끼워 넣은 것.
   *
   * 왜 따로 두는가 — 그 둘은 **서버 대화에 없다**. 카드는 run 에 딸린 것이고 구분선은 앱이
   * 남기는 표시라, 대화를 새로 받아오면(loadMessages) 사라진다. 그래서 messages 는 서버
   * 사본으로 두고, 끼워 넣은 결과를 여기에 따로 만든다. 화면은 이쪽만 그린다.
   */
  timeline: ChatMessage[];
  /** 대화를 한 번이라도 열어 메시지를 받아왔는지. 목록만 받은 세션은 false 다. */
  messagesLoaded: boolean;
  /**
   * 더 오래된 메시지를 받아올 커서. null 이면 처음까지 다 받았다는 뜻이다.
   * 화면은 이 값으로 '이전 대화 더 보기'를 그릴지 정한다.
   */
  olderCursor: string | null;
  /** 다음 질문을 어떻게 답할지. 대화방을 옮기지 않고 이 값만 바뀐다. */
  responseMode: ApiResponseMode;
  /** STYLIST 일 때 답할 스타일리스트들 (1~3명). 끄더라도 지우지 않는다 — 다시 켜면 복원한다. */
  selectedPersonaIds: StylistId[];
  updatedAt: number;
};

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** 목록의 시각 표시. '방금 / n분 전 / n시간 전 / 어제 / n일 전 / M월 D일' */
export function formatRelativeTime(ts: number, now: number = Date.now()): string {
  const diff = Math.max(0, now - ts);
  if (diff < MINUTE) return '방금';
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}분 전`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}시간 전`;
  const days = Math.floor(diff / DAY);
  if (days === 1) return '어제';
  if (days < 7) return `${days}일 전`;
  const d = new Date(ts);
  return `${d.getMonth() + 1}월 ${d.getDate()}일`;
}

/* 검색은 서버가 한다 (useSessionSearch). 앱이 받아둔 대화만 훑으면 한 번도 열지 않은
   대화가 제목으로만 걸려, 사용자에게는 "분명 그 말을 했는데 안 찾아진다"로 보인다. */

/**
 * 사용자가 고른 참고용 개인·공유 옷. 선택 시트 → 입력창 미리보기 → 전송까지 이 모양으로 들고 다닌다.
 *
 * 식별자는 referenceType에 따라 SharedWardrobeItem.id 또는 WardrobeItem.id다.
 * 나머지 필드는 **화면에 보여주기 위한 것**이고 서버 식별에는 쓰지 않는다(이름으로 찾지 않는다).
 */
export type ChatReferencePick = {
  referenceType: 'SHARED_WARDROBE_ITEM' | 'WARDROBE_ITEM';
  referenceItemId: string;
  imageUrl: string | null;
  itemName: string;
  ownerName: string;
  roomId?: string;
  roomName?: string;
};

export type ChatSendOptions = {
  wardrobeScope?: ApiWardrobeScope;
  reference?: ChatReferencePick;
};

/**
 * 카드에 붙일 '무엇과 비슷한지' 배지.
 *
 * 옷장 아이템을 참고하지 않은 추천은 서버가 빈 객체를 주므로 여기서 null 이 된다.
 * **모르는 값이 오면 배지를 만들지 않는다** — 지어내느니 생략하는 쪽이 맞고,
 * 카드 자체는 그대로 그린다(요구사항 7장).
 */
export type ReferenceBadge = {
  label: string;
  /** 시각 유사 기준에 못 미쳐 스타일로 대신 찾은 경우. 실패가 아니라 정상 결과다. */
  isStyleFallback: boolean;
  /** 상세 화면에서만 보여줄 근거 문장 */
  reasons: string[];
};

/** 스타일 fallback 안내 — 오류가 아니라는 게 문장에서 읽혀야 한다. */
export const STYLE_FALLBACK_NOTE =
  '겉모습이 충분히 비슷한 내 옷이 없어 스타일·색상·핏·소재가 가까운 옷을 골랐어요.';

export function toReferenceBadge(match: ApiReferenceMatch | undefined): ReferenceBadge | null {
  return buildReferenceBadge(match);
}

/**
 * 공유 옷 참고가 실패한 이유를 사용자 말로 옮긴다.
 *
 * ⚠️ 실패했다고 **이름 기반 일반 추천으로 조용히 넘어가지 않는다.** 그러면 사용자는
 *    참고가 반영된 추천을 받았다고 오해한다. 원인을 말하고 다시 고르게 한다.
 */
export function referenceErrorText(code: ApiReferenceErrorCode | null, fallback: string): string {
  switch (code) {
    case 'REFERENCE_ITEM_NOT_FOUND':
      return '선택한 공유 옷을 찾을 수 없어요. 다른 옷을 선택해 주세요.';
    case 'REFERENCE_ITEM_FORBIDDEN':
      return '이 공유 옷을 더 이상 참고할 수 없어요.';
    case 'REFERENCE_ITEM_NOT_READY':
      return '옷 이미지 처리가 끝난 뒤 다시 시도해 주세요.';
    case 'REFERENCE_ITEM_INVALID':
      return '이 옷은 참고할 수 없어요. 다른 옷을 선택해 주세요.';
    default:
      return fallback;
  }
}

/* ── 서버 응답 옮기기 ───────────────────────────────── */

function toRecMessage(
  messageId: string,
  resultId: string,
  card: ApiRecommendationCard,
): ChatMessage {
  return {
    id: `${messageId}-r${card.card_id}`,
    role: 'ai',
    kind: 'rec',
    resultId,
    cardId: card.card_id,
    /* 서버가 코디에 이름을 붙이지 않는다. 없는 이름을 지어내면 추천마다 다른 작명 규칙이
       생기므로 순위를 그대로 쓴다. */
    title: `추천 코디 ${card.rank}`,
    tags: recommendationCategoryTags(card.items),
    items: card.items.map((i) => ({
      id: i.item_id,
      name: i.display_name,
      category: i.category,
      imageUrl: itemImageUrl(i),
      price: i.price_snapshot,
      fromWardrobe: i.source_type !== 'PRODUCT',
    })),
    totalPrice: card.total_product_price,
    warnings: card.warnings ?? [],
    rationale: card.rationale ?? '',
    referenceBadge: toReferenceBadge(card.reference_match),
  };
}

/**
 * 무드 분석이 끝나면 서버가 **답변 메시지**를 하나 남긴다
 * (metadata.message_kind === 'mood', "사진에서 … 무드가 보여요. 반영할까요?").
 *
 * 그 메시지를 글 말풍선으로 그리는 대신 카드로 바꾼다. 카드가 같은 내용에 태그와
 * 선택 버튼까지 담고 있어서, 둘 다 그리면 같은 말이 연달아 두 번 나온다.
 *
 * 결정 상태(APPROVED/REJECTED)는 메시지가 아니라 **첨부**에 남으므로 밖에서 찾아 넣는다.
 */
function toMoodMessage(
  api: ApiChatMessage,
  decisions: Map<string, ApiMoodDecision | null>,
): ChatMessage | null {
  const meta = api.metadata ?? {};
  if (meta.message_kind !== 'mood') return null;
  const analysis = (meta.mood_analysis ?? {}) as Partial<ApiMoodAnalysis>;
  const tags = analysis.tags ?? [];
  const attachmentId = typeof meta.attachment_id === 'string' ? meta.attachment_id : '';
  if (tags.length === 0 || !attachmentId) return null;

  const decided = decisions.get(attachmentId);
  return {
    id: api.id,
    role: 'ai',
    kind: 'mood',
    attachmentId,
    tags,
    summary: analysis.summary ?? '',
    // UNDECIDED 와 null 은 같은 뜻으로 다룬다 — 아직 안 고른 것.
    decision: decided === 'APPROVED' || decided === 'REJECTED' ? decided : null,
  };
}

/** 첨부에만 있는 결정 상태를 attachment_id 로 찾을 수 있게 모은다. */
function collectDecisions(list: ApiChatMessage[]): Map<string, ApiMoodDecision | null> {
  const map = new Map<string, ApiMoodDecision | null>();
  for (const m of list) {
    for (const a of m.attachments) map.set(a.id, a.mood_decision);
  }
  return map;
}

/**
 * 서버 메시지 → 말풍선.
 * SYSTEM·TOOL 은 사람에게 보여줄 말이 아니라 버린다. 사진 첨부는 사진 말풍선을 따로 만들어
 * 글보다 앞에 놓는다 — 올릴 때 사진이 먼저였으니 다시 열어도 그 순서여야 한다.
 * 추천 카드는 말풍선 **뒤에** 붙는다 (먼저 말로 설명하고 그다음 코디를 보여주는 순서).
 */
function toMessages(
  api: ApiChatMessage,
  cards: ApiRecommendationCard[] = [],
  decisions: Map<string, ApiMoodDecision | null> = new Map(),
  wardrobeBased = false,
): ChatMessage[] {
  if (api.role !== 'USER' && api.role !== 'ASSISTANT') return [];
  const role = api.role === 'USER' ? 'user' : 'ai';
  const out: ChatMessage[] = [];

  /* 무드 카드는 말풍선을 대신한다 — 서버 문장("…무드가 보여요. 반영할까요?")과 카드가
     같은 말을 하므로 둘 다 그리면 같은 질문이 두 번 나온다. */
  if (role === 'ai') {
    const mood = toMoodMessage(api, decisions);
    if (mood) return [mood];
  }

  if (role === 'user') {
    for (const a of api.attachments) {
      out.push({ id: `${api.id}-a${a.id}`, role: 'user', kind: 'image', uri: a.image_url ?? undefined });
    }
  }

  // 무드 답변은 글 대신 카드로 그린다 (toMoodMessage 주석 참고).
  const mood = role === 'ai' ? toMoodMessage(api, decisions) : null;
  if (mood) {
    out.push(mood);
    return out;
  }

  const text = api.content.trim();
  /* 참고한 공유 옷이 있으면 글 말풍선 대신 참고 말풍선을 그린다 — 카드가 요청 문장까지
     담고 있어서 둘 다 그리면 같은 말이 두 번 나온다(무드 카드와 같은 이유). */
  const ref = role === 'user' ? api.reference_summary : null;
  if (ref) {
    out.push({
      id: api.id,
      role: 'user',
      ...buildReferenceBubble(ref, text),
    });
  } else if (text) {
    out.push({ id: api.id, role, kind: 'text', text });
  }
  /* 카드가 있다는 건 이 답변에 추천 id 가 붙어 있다는 뜻이다(카드를 그걸로 받아왔다).
     상세·피드백 API 가 result 와 card 둘 다 요구해서 카드에 함께 실어 둔다. */
  const resultId = recommendationIdOf(api);
  if (resultId) {
    for (const card of cards) out.push(toRecMessage(api.id, resultId, card));
  }

  /* 답변 생성이 실패하면 서버가 **질문 메시지**를 FAILED 로 표시한다(답변 메시지는 아예 없다).
     그 표시를 읽어 오류 줄을 만들면 대화를 다시 열어도 남는다.
     사유까지는 run 에만 있어 여기서는 알 수 없다 — 보낸 직후에는 sendText 가 채워 넣는다. */
  if (role === 'user' && api.status === 'FAILED') {
    out.push({
      id: failureLineId(api.id),
      role: 'ai',
      kind: 'error',
      text: wardrobeBased ? WARDROBE_UNAVAILABLE_MESSAGE : GENERIC_FAILURE,
      action: wardrobeBased ? 'OPEN_WARDROBE' : undefined,
    });
  }
  return out;
}

const GENERIC_FAILURE = '답변을 만들지 못했어요.';
export const WARDROBE_UNAVAILABLE_CODE = 'WARDROBE_OUTFIT_UNAVAILABLE';
export const WARDROBE_UNAVAILABLE_MESSAGE =
  '코디를 완성하기에 옷장에 준비된 옷이 부족해요. 옷을 조금 더 추가하면 어울리는 조합을 추천해드릴게요.';

function failureLineId(messageId: string): string {
  return `${messageId}-err`;
}

/** 답변에 붙은 추천 id. 없으면 그냥 대화만 오간 것이다. */
function recommendationIdOf(api: ApiChatMessage): string | null {
  const id = api.metadata?.recommendation_result_id;
  return typeof id === 'string' && id ? id : null;
}

/**
 * 추천이 붙은 답변들의 코디 카드를 한꺼번에 받아 메시지 id 별로 묶는다.
 *
 * 실패해도 대화 자체는 보여줘야 하므로 카드만 조용히 빠뜨린다 — 추천 조회 한 번이 실패했다고
 * 주고받은 말까지 사라지면 무엇이 잘못됐는지 알 수 없다.
 */
async function fetchCards(list: ApiChatMessage[]): Promise<Map<string, ApiRecommendationCard[]>> {
  const targets = list
    .map((m) => ({ messageId: m.id, resultId: recommendationIdOf(m) }))
    .filter((t): t is { messageId: string; resultId: string } => t.resultId !== null);

  const byMessage = new Map<string, ApiRecommendationCard[]>();
  if (targets.length === 0) return byMessage;

  // 같은 추천을 두 메시지가 가리킬 수 있어 결과별로 한 번만 부른다.
  const unique = [...new Set(targets.map((t) => t.resultId))];
  const results = await Promise.all(
    unique.map((id) =>
      getRecommendationResult(id)
        .then((r) => [id, r.cards] as const)
        .catch(() => [id, [] as ApiRecommendationCard[]] as const),
    ),
  );
  const cardsByResult = new Map(results);
  for (const t of targets) byMessage.set(t.messageId, cardsByResult.get(t.resultId) ?? []);
  return byMessage;
}

/** 스타일리스트 답변은 metadata에 run_id와 recommendation_result_ids 배열을 함께 남긴다. */
function stylistRunIdOf(message: ApiChatMessage): string | null {
  if (message.role !== 'ASSISTANT') return null;
  if (!Array.isArray(message.metadata?.recommendation_result_ids)) return null;
  const runId = message.metadata?.run_id;
  return typeof runId === 'string' && runId ? runId : null;
}

/** 다시 연 대화에서도 스타일리스트 카드를 복원할 수 있도록 메시지가 가리키는 run을 조회한다. */
async function fetchStylistRuns(list: ApiChatMessage[]): Promise<Map<string, ApiStylistRun>> {
  const targets = list
    .map((message) => ({ messageId: message.id, runId: stylistRunIdOf(message) }))
    .filter((target): target is { messageId: string; runId: string } => target.runId !== null);
  if (targets.length === 0) return new Map();

  const uniqueRunIds = [...new Set(targets.map((target) => target.runId))];
  const fetched = await Promise.all(
    uniqueRunIds.map((runId) =>
      getStylistRun(runId)
        .then((run) => [runId, run] as const)
        .catch(() => [runId, null] as const),
    ),
  );
  const byRunId = new Map(fetched);
  const byMessage = new Map<string, ApiStylistRun>();
  for (const target of targets) {
    const run = byRunId.get(target.runId);
    if (run) byMessage.set(target.messageId, run);
  }
  return byMessage;
}

function toSession(api: ApiChatSession, previous?: ChatSession): ChatSession {
  return {
    id: api.id,
    mode: fromApiMode(api.mode),
    title: api.title,
    // 목록 갱신이 이미 받아둔 대화를 지우면 안 된다.
    messages: previous?.messages ?? [],
    timeline: previous?.timeline ?? [],
    messagesLoaded: previous?.messagesLoaded ?? false,
    olderCursor: previous?.olderCursor ?? null,
    /* ⚠️ 서버가 이 필드를 **안 줄 수도 있다**(배포 서버가 아직 스타일리스트 이전 버전).
       없을 때 DEFAULT 로 덮으면 방금 켠 모드가 목록 새로고침 한 번에 꺼진다. */
    responseMode: api.response_mode ?? previous?.responseMode ?? 'DEFAULT',
    selectedPersonaIds: api.selected_persona_ids ?? previous?.selectedPersonaIds ?? [],
    updatedAt: new Date(api.last_message_at || api.updated_at).getTime(),
  };
}

/* ── 받아둔 원본 ─────────────────────────────────────
   말풍선(ChatMessage)만 들고 있으면 페이지를 이어 붙일 수 없다 — 말풍선에는 sequence 가
   없어서 "어디까지 이미 갖고 있는지"를 알 수 없고, 서버 메시지 하나가 말풍선 여러 개로
   갈라지기도 한다. 그래서 서버가 준 모양 그대로 세션별로 보관하고, 화면용 말풍선은
   여기서 매번 다시 만든다(rebuild). */
const rawMessages = new Map<string, ApiChatMessage[]>();
const rawCards = new Map<string, Map<string, ApiRecommendationCard[]>>();

/** 한 번에 받아올 메시지 수. 서버 상한은 100이다. */
const MESSAGE_PAGE_SIZE = 50;
/** 검색 한 페이지. 서버 상한은 50이다. */
const SEARCH_PAGE_SIZE = 20;

/** 한 세션의 원본을 지운다. 대화를 지웠을 때 남겨두면 같은 id 가 다시 생겨도 옛 내용이 붙는다. */
function forgetRaw(id: string): void {
  rawMessages.delete(id);
  rawCards.delete(id);
  overlays.delete(id);
}

/** 받아둔 원본 → 말풍선. 페이지를 더 받거나 결정이 바뀔 때마다 다시 만든다. */
function rebuild(id: string): void {
  const list = rawMessages.get(id) ?? [];
  const cards = rawCards.get(id) ?? new Map<string, ApiRecommendationCard[]>();
  const decisions = collectDecisions(list);
  const wardrobeBased = sessions.find((session) => session.id === id)?.mode === 'closet';
  replaceSession(id, (s) => ({
    ...s,
    messages: list.flatMap((m) =>
      toMessages(m, cards.get(m.id), decisions, wardrobeBased),
    ),
    messagesLoaded: true,
  }));
}

/**
 * 새로 받은 페이지를 이미 갖고 있던 원본에 합친다.
 *
 * sequence 로 겹치는 구간을 걷어내고 순서대로 다시 세운다. 재전송·재조회로 같은 메시지가
 * 두 번 오는 일이 있고(질문을 보낸 뒤 최신 페이지를 다시 받는다), 그때 말풍선이 두 벌
 * 생기면 대화가 반복된 것처럼 보인다.
 */
function mergeRaw(id: string, incoming: ApiChatMessage[]): void {
  const arrived = new Set(incoming.map((m) => m.sequence));
  const merged = [
    ...(rawMessages.get(id) ?? []).filter((m) => !arrived.has(m.sequence)),
    ...incoming,
  ].sort((a, b) => a.sequence - b.sequence);
  rawMessages.set(id, merged);
}

function mergeCards(id: string, incoming: Map<string, ApiRecommendationCard[]>): void {
  const current = rawCards.get(id) ?? new Map<string, ApiRecommendationCard[]>();
  for (const [messageId, cards] of incoming) current.set(messageId, cards);
  rawCards.set(id, current);
}

/**
 * 새로 받은 최근 묶음이 이미 갖고 있는 구간과 이어지는지.
 *
 * 대화를 열어 최근 50개를 받아둔 뒤 다른 기기에서 50개 넘게 오갔다면, 다시 받은 최근
 * 50개는 갖고 있던 것보다 **한참 뒤**라 사이에 못 받은 메시지가 생긴다. 그걸 그냥 이어
 * 붙이면 대화 중간이 조용히 비어버린다 — 사용자에게는 안 한 말을 한 것처럼 보인다.
 */
function canStitch(id: string, incoming: ApiChatMessage[]): boolean {
  const held = rawMessages.get(id) ?? [];
  if (held.length === 0 || incoming.length === 0) return false;
  return incoming[0].sequence <= held[held.length - 1].sequence + 1;
}

/* ── 타임라인에 끼워 넣는 것들 ───────────────────────
   서버 메시지 배열에 직접 들어있지 않은 모드 구분선·스타일리스트 카드를 세션별로 들고 있다가
   messages 사이에 끼워 넣는다. 붙는 자리는 **바로 앞 말풍선의 id** 로 기억한다 —
   대화를 다시 받아와도 그 말풍선은 같은 id 로 돌아오므로 자리를 잃지 않는다.
   스타일리스트 카드는 메시지 metadata의 run_id로 다시 조회해 복원하고, 화면에서만 만든
   모드 구분선은 현재 실행 동안만 유지한다. */

type Overlay = { id: string; after: string | null; message: ChatMessage };

const overlays = new Map<string, Overlay[]>();

function overlaysOf(sessionId: string): Overlay[] {
  return overlays.get(sessionId) ?? [];
}

/** messages 에 끼워 넣어 화면에 그릴 순서를 만든다. */
function buildTimeline(sessionId: string, messages: ChatMessage[]): ChatMessage[] {
  const list = overlaysOf(sessionId);
  if (list.length === 0) return messages;

  const byAnchor = new Map<string, ChatMessage[]>();
  const head: ChatMessage[] = [];
  const anchors = new Set(messages.map((m) => m.id));
  /* 앵커를 못 찾은 것 = 방금 만들어져 아직 서버 대화에 없는 말풍선에 붙은 경우.
     맨 뒤로 보낸다 — 실제로도 지금 대화의 끝이다. */
  const orphans: ChatMessage[] = [];

  for (const o of list) {
    if (o.after === null) head.push(o.message);
    else if (anchors.has(o.after)) {
      const bucket = byAnchor.get(o.after) ?? [];
      bucket.push(o.message);
      byAnchor.set(o.after, bucket);
    } else orphans.push(o.message);
  }

  const out: ChatMessage[] = [...head];
  for (const m of messages) {
    out.push(m);
    const attached = byAnchor.get(m.id);
    if (attached) out.push(...attached);
  }
  return [...out, ...orphans];
}

/** 끼워 넣은 것이 바뀌었을 때 화면용 순서를 다시 만든다 (replaceSession 이 알아서 계산한다). */
function rebuildTimeline(sessionId: string) {
  replaceSession(sessionId, (s) => s);
}

function addOverlay(sessionId: string, overlay: Overlay) {
  overlays.set(sessionId, [...overlaysOf(sessionId), overlay]);
  rebuildTimeline(sessionId);
}

function updateOverlay(sessionId: string, overlayId: string, message: ChatMessage) {
  overlays.set(
    sessionId,
    overlaysOf(sessionId).map((o) => (o.id === overlayId ? { ...o, message } : o)),
  );
  rebuildTimeline(sessionId);
}

/** 끼워 넣은 것을 걷어낸다 (되묻기로 카드가 필요 없어졌을 때). */
function removeOverlay(sessionId: string, overlayId: string) {
  overlays.set(
    sessionId,
    overlaysOf(sessionId).filter((o) => o.id !== overlayId),
  );
  rebuildTimeline(sessionId);
}

/** 붙는 자리를 옮긴다 — 답변까지 받고 나면 카드는 그 답변 **뒤**에 있어야 한다. */
function reanchorOverlay(sessionId: string, overlayId: string, after: string | null) {
  overlays.set(
    sessionId,
    overlaysOf(sessionId).map((o) => (o.id === overlayId ? { ...o, after } : o)),
  );
  rebuildTimeline(sessionId);
}

function lastMessageId(sessionId: string): string | null {
  const session = sessions.find((s) => s.id === sessionId);
  const list = session?.messages ?? [];
  return list.length > 0 ? list[list.length - 1].id : null;
}

/** 스타일리스트 묶음 안의 카드 하나만 손본다. */
function patchCard(
  message: ChatMessage,
  personaId: StylistId,
  patch: (card: StylistCard) => StylistCard,
): ChatMessage {
  if (message.kind !== 'stylist') return message;
  return {
    ...message,
    cards: message.cards.map((c) => (c.personaId === personaId ? patch(c) : c)),
  };
}

/* ── 스타일리스트 결과 옮기기 ───────────────────────── */

function toStylistCard(r: ApiPersonaResult): StylistCard {
  const card = r.card;
  return {
    personaId: r.persona_id,
    name: r.display_name || stylistStore.displayName(r.persona_id),
    order: r.display_order,
    status: r.status,
    message: r.message,
    reasonCodes: r.validated_reason_codes ?? [],
    items:
      card?.items.map((i) => ({
        id: i.item_id,
        name: i.display_name,
        category: i.category,
        imageUrl: itemImageUrl(i),
        price: i.price_snapshot,
        fromWardrobe: i.source_type !== 'PRODUCT',
      })) ?? [],
    totalPrice: card?.total_product_price ?? null,
    warnings: card?.warnings ?? [],
    resultId: r.result_id,
    cardId: card?.card_id ?? null,
    errorCode: r.error?.code || null,
    errorText: r.error?.message || null,
    alternating: r.alternative_status === 'PENDING' || r.alternative_status === 'RUNNING',
    alternativeCount: r.alternative_count,
    saved: card?.is_saved ?? false,
    renderStatus: card?.image?.status ?? null,
    renderImageUrl: card?.image?.image_url ?? null,
    renderErrorText: card?.image?.error?.message ?? null,
    referenceBadge: toReferenceBadge(card?.reference_match),
  };
}

/** 아직 아무것도 안 받은 자리 — 인원수만큼 먼저 깔아 두는 로딩 카드. */
function pendingCard(personaId: StylistId): StylistCard {
  return {
    personaId,
    name: stylistStore.displayName(personaId),
    order: stylistStore.displayOrder(personaId),
    status: 'PENDING',
    message: '',
    reasonCodes: [],
    items: [],
    totalPrice: null,
    warnings: [],
    resultId: null,
    cardId: null,
    errorCode: null,
    errorText: null,
    alternating: false,
    alternativeCount: 0,
    saved: false,
    renderStatus: null,
    renderImageUrl: null,
    renderErrorText: null,
    /* 아직 결과가 없으니 배지도 없다. */
    referenceBadge: null,
  };
}

function toStylistMessage(id: string, runId: string, run: ApiStylistRun): ChatMessage {
  return {
    id,
    role: 'ai',
    kind: 'stylist',
    runId,
    cards: [...run.results].sort((a, b) => a.display_order - b.display_order).map(toStylistCard),
  };
}

/** 한 run 의 카드 묶음은 하나뿐이라 id 를 run 에서 바로 만든다 (재시도·다른추천이 다시 찾는다). */
function stylistOverlayId(runId: string): string {
  return `sty-${runId}`;
}

/**
 * 스타일리스트 답변 한 턴.
 *
 * **인원수만큼 빈 카드를 먼저 깔고** 시작한다 — 다 끝난 뒤 한 번에 그리면 먼저 끝난 카드가
 * 남을 기다리는 동안 화면이 비고, 몇 장이 올지도 알 수 없다. 서버도 run 을 만들 때 자리를
 * 먼저 만들어 두므로 화면이 그 모양을 그대로 따른다.
 */
async function runStylistTurn(
  sessionId: string,
  runId: string,
  personaIds: StylistId[],
  question: string,
): Promise<{ run: ApiStylistRun; overlayId: string }> {
  const overlayId = stylistOverlayId(runId);
  const ordered = stylistStore.sortIds(personaIds);

  addOverlay(sessionId, {
    id: overlayId,
    // 방금 띄운 내 말풍선 뒤. 답변이 들어오면 sendText 가 그 뒤로 옮긴다.
    after: lastMessageId(sessionId),
    message: {
      id: overlayId,
      role: 'ai',
      kind: 'stylist',
      runId,
      cards: ordered.map(pendingCard),
    },
  });

  try {
    const run = await waitForStylistRun(runId, {
      hint: { personaIds: ordered, question },
      onProgress: (r) => {
        if (r.results.length === 0) return; // 아직 자리가 안 생겼다 — 깔아 둔 카드를 지우지 않는다
        updateOverlay(sessionId, overlayId, toStylistMessage(overlayId, runId, r));
      },
    });
    if (run.results.length > 0) {
      updateOverlay(sessionId, overlayId, toStylistMessage(overlayId, runId, run));
    } else if (run.status === 'FAILED') {
      // 자리조차 안 생기고 run 이 죽었다 = 스타일리스트 실행 자체가 실패
      failPendingCards(
        sessionId,
        overlayId,
        run.error_message || GENERIC_FAILURE,
        run.error_code || null,
      );
    } else {
      /* 결과 자리가 사라졌는데 run 은 정상으로 끝났다 = **되물은 것**이다.
         서버가 되묻기로 방향을 틀 때 아직 시작 안 한 스타일리스트 실행을 지운다
         (orchestrator 의 _discard_unstarted_persona_executions). 그러면 답할 사람이
         없으니 깔아 둔 카드도 치운다 — 실패로 남겨 두면 "답변을 만들지 못했어요" 가
         뜨는데, 실제로는 되묻는 말풍선이 정상으로 와 있어 두 말이 서로 어긋난다.
         사용자가 되물음에 답하면 그때 새 run 이 생기고 카드도 다시 깔린다. */
      removeOverlay(sessionId, overlayId);
    }
    return { run, overlayId };
  } catch (e) {
    /* 시간 초과 등으로 기다리기를 포기했다. 깔아 둔 카드를 그대로 두면 영영 도는 것처럼
       보이므로 실패로 바꿔 놓고 예외는 그대로 올린다(화면이 토스트로 알린다). */
    failPendingCards(sessionId, overlayId, messageOf(e, GENERIC_FAILURE));
    throw e;
  }
}

/** 아직 안 끝난 카드들을 실패로 바꾼다. 이미 받은 카드는 건드리지 않는다. */
function failPendingCards(
  sessionId: string,
  overlayId: string,
  text: string,
  errorCode: string | null = null,
) {
  const current = overlaysOf(sessionId).find((o) => o.id === overlayId)?.message;
  if (!current || current.kind !== 'stylist') return;
  updateOverlay(sessionId, overlayId, {
    ...current,
    cards: current.cards.map((c) =>
      c.status === 'PENDING' || c.status === 'RUNNING'
        ? { ...c, status: 'FAILED', errorCode, errorText: text }
        : c,
    ),
  });
}

/** 폴링 중간 상태를 카드에 반영한다. 재시도·다른추천이 공유한다. */
function applyRunProgress(sessionId: string, runId: string, run: ApiStylistRun) {
  if (run.results.length === 0) return;
  const overlayId = stylistOverlayId(runId);
  updateOverlay(sessionId, overlayId, toStylistMessage(overlayId, runId, run));
}

/** 메시지와 함께 다시 조회한 run을 타임라인 카드로 복원하거나 최신 상태로 교체한다. */
function mergeStylistOverlays(
  sessionId: string,
  runsByMessage: Map<string, ApiStylistRun>,
): void {
  if (runsByMessage.size === 0) return;
  const current = [...overlaysOf(sessionId)];
  for (const [messageId, run] of runsByMessage) {
    const overlayId = stylistOverlayId(run.id);
    const restored: Overlay = {
      id: overlayId,
      after: messageId,
      message: toStylistMessage(overlayId, run.id, run),
    };
    const index = current.findIndex((overlay) => overlay.id === overlayId);
    if (index >= 0) current[index] = restored;
    else current.push(restored);
  }
  overlays.set(sessionId, current);
}

const STYLIST_RENDER_POLL_MS = 1500;
const STYLIST_RENDER_TIMEOUT_MS = 2 * 60 * 1000;

function renderPatch(job: ApiRenderJob): Pick<
  StylistCard,
  'renderStatus' | 'renderImageUrl' | 'renderErrorText'
> {
  return {
    renderStatus: job.status,
    renderImageUrl: job.image_url,
    renderErrorText: job.error?.message ?? null,
  };
}

function updateStylistRender(
  sessionId: string,
  runId: string,
  personaId: StylistId,
  resultId: string,
  cardId: string,
  patch: Pick<StylistCard, 'renderStatus' | 'renderImageUrl' | 'renderErrorText'>,
): boolean {
  const overlayId = stylistOverlayId(runId);
  const current = overlaysOf(sessionId).find((o) => o.id === overlayId)?.message;
  if (!current || current.kind !== 'stylist') return false;
  const target = current.cards.find((card) => card.personaId === personaId);
  // 다른 추천으로 카드가 교체된 뒤 옛 이미지 폴링 결과가 새 카드에 붙지 않게 한다.
  if (target?.resultId !== resultId || target.cardId !== cardId) return false;
  updateOverlay(
    sessionId,
    overlayId,
    patchCard(current, personaId, (card) => ({ ...card, ...patch })),
  );
  return true;
}

async function watchStylistRender(
  sessionId: string,
  runId: string,
  personaId: StylistId,
  resultId: string,
  cardId: string,
): Promise<void> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < STYLIST_RENDER_TIMEOUT_MS) {
    await new Promise((resolve) => setTimeout(resolve, STYLIST_RENDER_POLL_MS));
    const job = await apiGetCardRenderStatus(resultId, cardId);
    if (!job) continue;
    if (!updateStylistRender(sessionId, runId, personaId, resultId, cardId, renderPatch(job))) {
      return;
    }
    if (job.status === 'SUCCEEDED' || job.status === 'FAILED') return;
  }
  updateStylistRender(sessionId, runId, personaId, resultId, cardId, {
    renderStatus: 'FAILED',
    renderImageUrl: null,
    renderErrorText: '이미지 생성 확인 시간이 초과됐어요. 다시 시도해 주세요.',
  });
}

async function startStylistRender(
  sessionId: string,
  runId: string,
  personaId: StylistId,
  resultId: string,
  cardId: string,
): Promise<boolean> {
  updateStylistRender(sessionId, runId, personaId, resultId, cardId, {
    renderStatus: 'QUEUED',
    renderImageUrl: null,
    renderErrorText: null,
  });
  try {
    const job = await apiRenderCard(resultId, cardId);
    updateStylistRender(sessionId, runId, personaId, resultId, cardId, renderPatch(job));
    if (job.status !== 'SUCCEEDED' && job.status !== 'FAILED') {
      void watchStylistRender(sessionId, runId, personaId, resultId, cardId).catch((error) => {
        updateStylistRender(sessionId, runId, personaId, resultId, cardId, {
          renderStatus: 'FAILED',
          renderImageUrl: null,
          renderErrorText: messageOf(error, '이미지 생성 상태를 확인하지 못했어요.'),
        });
      });
    }
    return job.status !== 'FAILED';
  } catch (error) {
    updateStylistRender(sessionId, runId, personaId, resultId, cardId, {
      renderStatus: 'FAILED',
      renderImageUrl: null,
      renderErrorText: messageOf(error, '코디 이미지를 만들지 못했어요.'),
    });
    return false;
  }
}

function sameIds(a: StylistId[], b: StylistId[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

/* ── 스토어 ─────────────────────────────────────────── */

let sessions: ChatSession[] = [];
let loading = false;
/**
 * 목록을 **한 번이라도** 받아왔는지. 빈 배열만으로는 "아직 안 불러옴"과 "정말 없음"을
 * 구분할 수 없어서, 첫 렌더에 "대화가 없어요" 화면이 한 프레임 번쩍이는 문제가 있었다.
 */
let loadedOnce = false;
let error: string | null = null;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((l) => l());
}

/** 최근 대화가 위로. 목록·검색이 모두 이 순서를 쓴다. */
function sortByRecent(list: ChatSession[]): ChatSession[] {
  return [...list].sort((a, b) => b.updatedAt - a.updatedAt);
}

/**
 * 세션 하나를 바꾼다.
 *
 * 화면용 순서(timeline)는 여기서 **항상** 다시 만든다. 말풍선을 건드리는 자리마다 따로
 * 챙기게 두면 한 곳만 빠뜨려도 "방금 보낸 말이 안 보이는" 상태가 된다 — 한 곳으로 모은다.
 */
function replaceSession(id: string, patch: (s: ChatSession) => ChatSession) {
  sessions = sessions.map((s) => {
    if (s.id !== id) return s;
    const next = patch(s);
    return { ...next, timeline: buildTimeline(id, next.messages) };
  });
  notify();
}

let messageSeq = 0;

/** 말풍선 id — 같은 밀리초에 여러 개가 추가돼도 겹치지 않게 순번을 붙인다. */
export function nextMessageId(): string {
  return `m${Date.now()}-${++messageSeq}`;
}

function messageOf(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/**
 * 목록을 다시 받아 서버가 정한 제목·순서에 맞춘다.
 * 실패해도 대화는 이미 화면에 있으니 조용히 넘어간다.
 */
async function syncSessionList(): Promise<void> {
  const fresh = await apiListSessions().catch(() => null);
  if (!fresh) return;
  const before = new Map(sessions.map((s) => [s.id, s]));
  sessions = sortByRecent(fresh.map((s) => toSession(s, before.get(s.id))));
  notify();
}

export const chatStore = {
  getSessions: () => sessions,

  /**
   * 로그아웃·탈퇴 때 기기에 남은 대화 흔적을 지운다.
   *
   * 목록과 주고받은 내용은 **메모리에만** 있어서, 지우지 않으면 다음 사람(게스트 포함)이
   * 채팅에 들어갔을 때 방금 나간 사람의 대화 목록이 그대로 보인다. 서버에서 새로
   * 받아오기 전까지 남아 있기 때문이다.
   *
   * loadedOnce 도 되돌린다 — 남겨 두면 빈 목록이 '정말 대화가 없음'으로 읽혀
   * 로그인 직후 한 프레임 "대화가 없어요"가 번쩍인다.
   */
  reset(): void {
    sessions = [];
    loading = false;
    loadedOnce = false;
    error = null;
    rawMessages.clear();
    rawCards.clear();
    overlays.clear();
    setStatus();
    notify();
  },

  getSession: (id: string | undefined) =>
    id ? sessions.find((s) => s.id === id) : undefined,
  getStatus: () => status,

  /** 목록 새로고침. 화면 진입·당겨서 새로고침에서 부른다. */
  async loadSessions(): Promise<void> {
    loading = true;
    error = null;
    setStatus();
    try {
      const list = await apiListSessions();
      const before = new Map(sessions.map((s) => [s.id, s]));
      sessions = sortByRecent(list.map((s) => toSession(s, before.get(s.id))));
    } catch (e) {
      error = messageOf(e, '대화 목록을 불러오지 못했어요');
    } finally {
      loading = false;
      loadedOnce = true;
      setStatus();
      notify();
    }
  },

  /**
   * 대화 내용 받아오기 — **최근 한 묶음**만 받는다. 이미 받아둔 세션은 건너뛴다(force 로 강제).
   *
   * 전체를 한 번에 받던 때는 대화가 길어질수록 열 때마다 느려졌고, 첨부·추천이 붙은
   * 메시지는 카드 조회까지 그만큼 늘어났다. 더 예전 대화는 화면에서 눌러 받아온다
   * (loadOlderMessages).
   *
   * force 로 다시 받아도 이미 받아둔 예전 페이지는 지우지 않는다 — 질문 하나 보낼 때마다
   * 스크롤이 최근 묶음으로 잘려나가면 방금 읽던 자리를 잃는다.
   */
  async loadMessages(id: string, options: { force?: boolean } = {}): Promise<void> {
    const current = sessions.find((s) => s.id === id);
    if (!options.force && current?.messagesLoaded) return;

    const page = await apiPageMessages(id, { limit: MESSAGE_PAGE_SIZE });
    const [cards, stylistRuns] = await Promise.all([
      fetchCards(page.items),
      fetchStylistRuns(page.items),
    ]);

    if (canStitch(id, page.items)) {
      /* 이미 갖고 있던 구간과 이어진다. 그때의 커서가 여전히 '그보다 더 오래된' 자리를
         가리키므로 그대로 둔다 — 이번 응답의 커서는 최근 묶음 기준이라 덮어쓰면 중간이 빈다. */
      mergeCards(id, cards);
      mergeRaw(id, page.items);
    } else {
      /* 이어지지 않는다 = 갖고 있던 구간과 이번 묶음 사이에 못 받은 메시지가 있다
         (다른 기기에서 한참 대화했을 때). 중간이 빈 대화를 보여주느니 최근 묶음만
         남기고 '이전 대화 더 보기'로 되돌린다. */
      rawMessages.set(id, page.items);
      rawCards.set(id, cards);
      replaceSession(id, (s) => ({ ...s, olderCursor: page.next_cursor ?? null }));
    }
    mergeStylistOverlays(id, stylistRuns);
    rebuild(id);
  },

  /** '이전 대화 더 보기'. 커서가 없으면(처음까지 다 받았으면) 아무 일도 하지 않는다. */
  async loadOlderMessages(id: string): Promise<void> {
    const cursor = sessions.find((s) => s.id === id)?.olderCursor;
    if (!cursor) return;
    const page = await apiPageMessages(id, {
      limit: MESSAGE_PAGE_SIZE,
      cursor,
    });
    const [cards, stylistRuns] = await Promise.all([
      fetchCards(page.items),
      fetchStylistRuns(page.items),
    ]);
    mergeCards(id, cards);
    mergeRaw(id, page.items);
    replaceSession(id, (s) => ({ ...s, olderCursor: page.next_cursor ?? null }));
    mergeStylistOverlays(id, stylistRuns);
    rebuild(id);
  },

  /**
   * 새 대화. 서버가 인사 메시지를 sequence 1 로 미리 넣어 주므로 여기서 만들지 않는다.
   * 제목도 서버가 첫 질문을 보고 정한다 — 그래서 만들 때는 비워 둔다.
   */
  async createSession(mode: ChatMode, options: { asGuest?: boolean } = {}): Promise<ChatSession> {
    const created = await apiCreateSession(toApiMode(mode), undefined, options);
    const session = toSession(created);
    sessions = [session, ...sessions];
    notify();
    // 인사 메시지를 바로 띄우기 위해 이어서 받아온다(실패해도 대화 진입은 막지 않는다).
    this.loadMessages(session.id).catch(() => {});
    return session;
  },

  /** 이름 바꾸기 — 화면을 먼저 바꾸고 서버에 반영한다. 실패하면 되돌린다. */
  async renameSession(id: string, title: string): Promise<void> {
    const next = title.trim();
    if (!next) return;
    const previous = sessions.find((s) => s.id === id)?.title;
    replaceSession(id, (s) => ({ ...s, title: next }));
    try {
      await apiRenameSession(id, next);
    } catch (e) {
      if (previous !== undefined) replaceSession(id, (s) => ({ ...s, title: previous }));
      throw e;
    }
  },

  /** 지우기 — 목록에서 먼저 걷어내고, 실패하면 되돌린다. */
  async removeSession(id: string): Promise<void> {
    const previous = sessions;
    sessions = sessions.filter((s) => s.id !== id);
    notify();
    try {
      await apiDeleteSession(id);
      forgetRaw(id);
    } catch (e) {
      sessions = previous;
      notify();
      throw e;
    }
  },

  /**
   * 질문 보내기. 말풍선을 먼저 띄우고(기다리는 동안 빈 화면이 되지 않게) 답변을 기다린다.
   *
   * 끝난 뒤 목록을 다시 받아오는 이유 — 답변 말풍선뿐 아니라 **서버가 정한 제목**도
   * 이때 확정된다(첫 질문으로 자동 저장). 화면이 따로 챙기지 않아도 되게 여기서 맞춘다.
   *
   * 되묻는 답변(NEEDS_CLARIFICATION)도 정상 답변이라 실패로 취급하지 않는다.
   */
  async sendText(
    id: string,
    text: string,
    options: ChatSendOptions = {},
  ): Promise<ApiChatRun> {
    const body = text.trim();
    if (!body) throw new Error('보낼 내용이 없어요');

    const session = sessions.find((s) => s.id === id);
    if (!session) throw new Error('대화를 찾을 수 없어요');
    const stylistMode =
      session.responseMode === 'STYLIST' && session.selectedPersonaIds.length > 0;

    const draftId = nextMessageId();
    replaceSession(id, (s) => ({
      ...s,
      messages: [...s.messages, { id: draftId, role: 'user', kind: 'text', text: body }],
      updatedAt: Date.now(),
    }));

    const reference = options.reference;
    let submitted;
    try {
      submitted = await apiSendMessage(
        id,
        body,
        newClientMessageId(),
        {
          wardrobeScope: options.wardrobeScope,
          reference: reference
            ? reference.referenceType === 'SHARED_WARDROBE_ITEM'
              ? {
                  type: 'SHARED_WARDROBE_ITEM',
                  shared_item_id: reference.referenceItemId,
                }
              : {
                  type: 'WARDROBE_ITEM',
                  wardrobe_item_id: reference.referenceItemId,
                }
            : undefined,
        },
      );
    } catch (e) {
      /* 참고 옷이 사라졌거나 권한이 없거나 아직 벡터 처리 전이다. 낙관적으로 띄운 말풍선을
         걷어내고 원인을 올린다 — 여기서 참고를 빼고 다시 보내면 사용자는 참고가 반영된
         추천을 받았다고 오해한다(요구사항 5장). */
      const code = referenceErrorCode(e);
      replaceSession(id, (s) => ({
        ...s,
        messages: s.messages.filter((m) => m.id !== draftId),
      }));
      if (code) throw new Error(referenceErrorText(code, messageOf(e, GENERIC_FAILURE)));
      throw e;
    }

    /* 기다리는 동안 보여줄 참고 말풍선. 곧 loadMessages 가 서버판(reference_summary)으로
       갈아 끼우므로 여기서는 방금 띄운 글 말풍선을 참고 말풍선으로 바꾸기만 한다 —
       둘 다 두면 같은 요청 문장이 두 번 나온다. */
    if (reference) {
      replaceSession(id, (s) => ({
        ...s,
        messages: s.messages.map((m) =>
          m.id === draftId
            ? {
                id: draftId,
                role: 'user',
                kind: 'reference',
                text: body,
                referenceType: reference.referenceType,
                referenceItemId: reference.referenceItemId,
                imageUrl: reference.imageUrl,
                itemName: reference.itemName,
                ownerName: reference.ownerName,
                roomName: reference.roomName,
              }
            : m,
        ),
      }));
    }

    /* 스타일리스트 모드면 답변을 기다리는 방식이 다르다 — 결과가 여러 개고 끝나는 시각이
       제각각이라, 다 끝날 때까지 묶어 두지 않고 끝난 카드부터 채운다. */
    const turn = stylistMode
      ? await runStylistTurn(id, submitted.run.id, session.selectedPersonaIds, body)
      : null;
    const run = turn ? turn.run : await waitForRun(submitted.run.id);

    // 답변이 생겼든 실패했든 서버가 가진 대화가 정답이다 — 통째로 다시 맞춘다.
    await this.loadMessages(id, { force: true }).catch(() => {});

    /* 카드는 답변 **뒤**에 와야 한다. 보낼 때는 그 답변 말풍선이 아직 없어서 임시로 끝에
       놓아 뒀고, 이제 서버 대화가 들어왔으니 마지막 말풍선 뒤로 옮긴다. */
    if (turn) reanchorOverlay(id, turn.overlayId, lastMessageId(id));

    /* 실패 사유는 run 에만 있고 대화에는 남지 않는다. 방금 보낸 질문의 오류 줄에만
       구체적인 사유를 채워 넣는다 — 다시 열면 일반 문구로 돌아간다(서버가 사유를 모르므로). */
    if (run.status === 'FAILED' && run.error_message) {
      const lineId = failureLineId(submitted.message.id);
      const wardrobeUnavailable =
        session.mode === 'closet' && run.error_code === WARDROBE_UNAVAILABLE_CODE;
      replaceSession(id, (s) => ({
        ...s,
        messages: s.messages.map((m) =>
          m.id === lineId && m.kind === 'error'
            ? {
                ...m,
                text: wardrobeUnavailable ? WARDROBE_UNAVAILABLE_MESSAGE : run.error_message,
                action: wardrobeUnavailable ? 'OPEN_WARDROBE' : undefined,
              }
            : m,
        ),
      }));
    }

    if (isAnswered(run.status)) await syncSessionList();
    return run;
  },

  /**
   * 사진 올리고 무드까지 읽어낸다. 올리기 → 분석 시작 → 분석 끝날 때까지 대기, 세 걸음이다.
   *
   * 중간에 한 번씩 대화를 다시 받아오는 이유 — 사진 말풍선은 올리자마자 보여야 하고,
   * 무드 카드는 분석이 끝나야 생긴다. 마지막에 한 번만 받아오면 사진이 늦게 뜬다.
   *
   * 분석이 실패해도 사진은 이미 대화에 남는다. 그래서 실패를 예외로 올려 화면이
   * 알리게 하되, 올린 사진까지 되돌리지는 않는다.
   */
  async attachPhoto(id: string, uri: string): Promise<void> {
    const uploaded = await apiUploadAttachment(id, { uri }, newClientMessageId());
    await this.loadMessages(id, { force: true }).catch(() => {});

    const started = await apiStartMoodAnalysis(id, uploaded.attachment.id);
    const run = await waitForRun(started.run.id);
    await this.loadMessages(id, { force: true }).catch(() => {});

    if (run.status === 'FAILED') {
      throw new Error(run.error_message || '사진에서 무드를 읽지 못했어요');
    }
  },

  /* ── 스타일리스트 모드 ───────────────────────────── */

  /**
   * 응답 모드 전환. 대화방을 옮기지도, 새로 만들지도 않는다 — **다음 질문부터** 달라진다.
   *
   * ⚠️ personaIds 를 **생략하면 서버가 복원한다**(세션 이전값 → 회원 마지막값 → minimal).
   *    빈 배열을 보내는 것과 다르니 "고른 게 없다"는 뜻으로 [] 를 넘기지 말 것.
   *
   * 바뀐 자리에는 구분선을 남긴다. 되돌아봤을 때 어디서부터 답하는 방식이 달라졌는지
   * 알 수 있어야 하기 때문이다. 아무것도 안 바뀌었으면 남기지 않는다.
   */
  async setResponseMode(
    id: string,
    mode: ApiResponseMode,
    personaIds?: StylistId[],
  ): Promise<void> {
    const before = sessions.find((s) => s.id === id);
    const normalizedPersonaIds =
      mode === 'STYLIST' && personaIds ? stylistStore.sortIds(personaIds) : personaIds;
    const updated = await apiUpdateResponseMode(id, mode, normalizedPersonaIds);
    const nextIds = updated.selected_persona_ids ?? [];

    const changed =
      before?.responseMode !== updated.response_mode ||
      (updated.response_mode === 'STYLIST' && !sameIds(before?.selectedPersonaIds ?? [], nextIds));

    replaceSession(id, (s) => ({
      ...s,
      responseMode: updated.response_mode,
      // 꺼도 선택값은 지우지 않는다 — 다시 켤 때 복원해야 한다.
      selectedPersonaIds: nextIds.length > 0 ? nextIds : s.selectedPersonaIds,
    }));

    if (!changed) return;
    const markId = nextMessageId();
    addOverlay(id, {
      id: markId,
      after: lastMessageId(id),
      message: {
        id: markId,
        role: 'ai',
        kind: 'mode',
        mode: updated.response_mode,
        names: updated.response_mode === 'STYLIST' ? stylistStore.displayNames(nextIds) : [],
      },
    });
  },

  /**
   * 실패한 스타일리스트 한 명만 다시 실행한다. 성공한 다른 카드는 그대로 남는다.
   * 같은 run 을 다시 폴링하되 **그 한 명만** 보고 기다린다 — 나머지는 이미 끝나 있어서
   * '전원 종료' 조건으로는 첫 폴링에 바로 빠져나온다.
   */
  async retryStylist(sessionId: string, runId: string, personaId: StylistId): Promise<void> {
    const overlayId = stylistOverlayId(runId);
    const current = overlaysOf(sessionId).find((o) => o.id === overlayId)?.message;
    if (current) {
      // 누른 것이 바로 보이게 먼저 대기 상태로 돌린다.
      updateOverlay(
        sessionId,
        overlayId,
        patchCard(current, personaId, (c) => ({
          ...c,
          status: 'PENDING',
          errorCode: null,
          errorText: null,
        })),
      );
    }

    const accepted = await apiRetryPersona(runId, personaId);
    applyRunProgress(sessionId, runId, accepted.run);
    /* 몇 번째 재실행인지를 기준으로 삼는다. 상태만 보면 접수 직후 아직 안 바뀐 옛 FAILED 를
       보고 "벌써 끝났다"고 오해할 수 있다. */
    const target = accepted.run.results.find((r) => r.persona_id === personaId);
    const expected = target?.retry_count ?? 0;

    await waitForStylistRun(runId, {
      onProgress: (run) => applyRunProgress(sessionId, runId, run),
      until: (run) => {
        const r = run.results.find((x) => x.persona_id === personaId);
        if (!r) return true;
        return r.retry_count >= expected && (r.status === 'SUCCEEDED' || r.status === 'FAILED');
      },
    });
  },

  /**
   * 같은 스타일리스트에게 다른 코디를 받는다.
   * 기다리는 동안 **지금 카드는 그대로 둔다** — 없애 버리면 마음에 들던 코디를 놓치고,
   * 새 추천이 실패하면 남는 게 없다.
   */
  async alternativeStylist(
    sessionId: string,
    runId: string,
    personaId: StylistId,
  ): Promise<void> {
    const overlayId = stylistOverlayId(runId);
    const current = overlaysOf(sessionId).find((o) => o.id === overlayId)?.message;
    if (current) {
      updateOverlay(
        sessionId,
        overlayId,
        patchCard(current, personaId, (c) => ({ ...c, alternating: true })),
      );
    }
    try {
      const accepted = await apiRequestAlternative(runId, personaId);
      applyRunProgress(sessionId, runId, accepted.run);
      const target = accepted.run.results.find((r) => r.persona_id === personaId);
      const expected = target?.alternative_count ?? 0;

      const completed = await waitForStylistRun(runId, {
        onProgress: (run) => applyRunProgress(sessionId, runId, run),
        until: (run) => {
          const r = run.results.find((x) => x.persona_id === personaId);
          if (!r) return true;
          return (
            r.alternative_count >= expected &&
            r.alternative_status !== 'PENDING' &&
            r.alternative_status !== 'RUNNING'
          );
        },
      });
      const completedTarget = completed.results.find((r) => r.persona_id === personaId);
      if (!completedTarget) throw new Error('다른 추천 상태를 확인하지 못했어요');
      if (completedTarget.alternative_status === 'FAILED') {
        throw new Error(
          completedTarget.alternative_error_message ||
            '다른 추천을 받지 못했어요. 잠시 후 다시 시도해 주세요.',
        );
      }
    } finally {
      // 요청 거절·네트워크 오류·폴링 시간 초과에서도 현재 카드를 다시 조작할 수 있어야 한다.
      const latest = overlaysOf(sessionId).find((o) => o.id === overlayId)?.message;
      if (latest) {
        updateOverlay(
          sessionId,
          overlayId,
          patchCard(latest, personaId, (c) => ({ ...c, alternating: false })),
        );
      }
    }
  },

  /** 고른 코디를 저장한 뒤 해당 카드 한 장의 이미지 생성만 접수한다. */
  async saveStylistCard(
    sessionId: string,
    runId: string,
    personaId: StylistId,
  ): Promise<{ renderStarted: boolean }> {
    const overlayId = stylistOverlayId(runId);
    const current = overlaysOf(sessionId).find((o) => o.id === overlayId)?.message;
    if (!current || current.kind !== 'stylist') throw new Error('추천 카드를 찾지 못했어요');
    const card = current.cards.find((c) => c.personaId === personaId);
    if (!card?.resultId || !card.cardId) throw new Error('아직 저장할 코디가 없어요');

    updateOverlay(sessionId, overlayId, patchCard(current, personaId, (c) => ({ ...c, saved: true })));
    try {
      await apiSaveCard(card.resultId, card.cardId);
      await savedLookStore.load();
    } catch (e) {
      const reverted = overlaysOf(sessionId).find((o) => o.id === overlayId)?.message;
      if (reverted) {
        updateOverlay(
          sessionId,
          overlayId,
          patchCard(reverted, personaId, (c) => ({ ...c, saved: false })),
        );
      }
      throw e;
    }
    return {
      renderStarted: await startStylistRender(
        sessionId,
        runId,
        personaId,
        card.resultId,
        card.cardId,
      ),
    };
  },

  /** 추천은 그대로 둔 채 실패한 이미지 작업만 다시 접수한다. */
  async retryStylistRender(
    sessionId: string,
    runId: string,
    personaId: StylistId,
  ): Promise<boolean> {
    const overlayId = stylistOverlayId(runId);
    const current = overlaysOf(sessionId).find((o) => o.id === overlayId)?.message;
    if (!current || current.kind !== 'stylist') return false;
    const card = current.cards.find((row) => row.personaId === personaId);
    if (!card?.resultId || !card.cardId) throw new Error('이미지를 만들 코디가 없어요');
    return startStylistRender(sessionId, runId, personaId, card.resultId, card.cardId);
  },

  /**
   * 무드 카드의 두 버튼.
   * APPROVE 면 사진에서 읽은 표준 태그가 세션 추천 조건에 들어가고, REJECT 면 분석 기록만
   * 남는다. 서버가 **첫 결정만** 받으므로(번복하면 409) 카드도 한 번만 바뀐다.
   */
  async decideMood(
    sessionId: string,
    attachmentId: string,
    decision: ApiMoodDecisionInput,
  ): Promise<void> {
    const result = await apiDecideMood(sessionId, attachmentId, decision);
    const decided: ApiMoodDecision =
      result.attachment.mood_decision ?? (decision === 'APPROVE' ? 'APPROVED' : 'REJECTED');
    /* 결정은 첨부에 달려 있다. 말풍선만 고치면 페이지를 더 받아 다시 그릴 때(rebuild)
       서버에서 온 옛 값으로 되돌아가 버튼이 되살아난다. */
    const list = rawMessages.get(sessionId);
    if (list) {
      rawMessages.set(
        sessionId,
        list.map((message) => ({
          ...message,
          attachments: message.attachments.map((attachment) =>
            attachment.id === attachmentId
              ? { ...attachment, mood_decision: decided }
              : attachment,
          ),
        })),
      );
    }
    rebuild(sessionId);
  },

  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

/* useSyncExternalStore 는 getSnapshot 이 매번 같은 참조를 주길 요구한다.
   loading·error 를 객체로 만들어 돌려주면 렌더마다 새 객체라 무한 루프가 된다.
   그래서 바뀔 때만 새로 만들어 둔다. */
let status: { loading: boolean; loadedOnce: boolean; error: string | null } = {
  loading: false,
  loadedOnce: false,
  error: null,
};
function setStatus() {
  if (status.loading !== loading || status.error !== error || status.loadedOnce !== loadedOnce) {
    status = { loading, loadedOnce, error };
  }
}

export function useChatSessions(): ChatSession[] {
  return useSyncExternalStore(chatStore.subscribe, chatStore.getSessions, chatStore.getSessions);
}

/** 목록 로딩·오류 상태. 빈 화면과 '못 불러옴'을 구분해 보여주기 위한 것. */
export function useChatStatus(): { loading: boolean; loadedOnce: boolean; error: string | null } {
  return useSyncExternalStore(chatStore.subscribe, chatStore.getStatus, chatStore.getStatus);
}

/** 세션 하나를 구독. 없는 id(삭제된 대화 등)면 undefined. */
export function useChatSession(id: string | undefined): ChatSession | undefined {
  const all = useChatSessions();
  return id ? all.find((s) => s.id === id) : undefined;
}

/** 가장 최근 대화 — id 없이 대화 화면으로 들어온 경우의 기본값. */
export function useLatestSession(): ChatSession | undefined {
  const all = useChatSessions();
  return sortByRecent(all)[0];
}

/** 검색 결과 한 줄. preview 는 검색어가 걸린 메시지이고, 제목만 걸렸으면 비어 있다. */
export type SearchedSession = {
  session: ChatSession;
  preview: string;
};

export type SessionSearchState = {
  items: SearchedSession[];
  /** 서버가 센 전체 건수. 지금 받아온 items 보다 클 수 있다. */
  totalCount: number;
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadMore: () => void;
};

/** 사용자가 글자를 칠 때마다 서버를 부르지 않도록 잠깐 기다린다. */
const SEARCH_DEBOUNCE_MS = 300;

function toSearched(item: ApiChatSessionSearchItem): SearchedSession {
  return {
    // 이미 받아둔 세션이 있으면 그 대화 내용을 유지한 채 제목·시각만 새로 맞춘다.
    session: toSession(item, sessions.find((s) => s.id === item.id)),
    preview: item.search_match?.preview ?? '',
  };
}

/**
 * 서버에서 대화를 찾는다 — 제목뿐 아니라 **저장된 메시지 본문**까지 걸린다.
 *
 * 검색어가 바뀌면 첫 페이지부터 다시 받는다. 서버가 커서에 검색어를 함께 서명해 두기
 * 때문이기도 하고, 이전 검색 결과가 남아 있으면 새 검색어의 결과처럼 읽히기 때문이다.
 *
 * 응답이 늦게 도착해 순서가 뒤집히는 일이 있어(짧은 검색어일수록 결과가 많아 느리다)
 * 요청마다 번호를 매기고 **마지막 요청의 응답만** 반영한다.
 */
export function useSessionSearch(query: string): SessionSearchState {
  const trimmed = query.trim();
  const [items, setItems] = useState<SearchedSession[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  useEffect(() => {
    if (!trimmed) {
      requestId.current += 1; // 진행 중인 검색의 응답을 버린다
      return;
    }

    const current = ++requestId.current;
    const timer = setTimeout(() => {
      setLoading(true);
      setError(null);
      apiSearchSessions(trimmed, { limit: SEARCH_PAGE_SIZE })
        .then((page) => {
          if (requestId.current !== current) return;
          setItems(page.items.map(toSearched));
          setTotalCount(page.total_count);
          setCursor(page.next_cursor ?? null);
        })
        .catch((e) => {
          if (requestId.current !== current) return;
          setError(messageOf(e, '검색하지 못했어요'));
          setItems([]);
          setTotalCount(0);
          setCursor(null);
        })
        .finally(() => {
          if (requestId.current === current) setLoading(false);
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [trimmed]);

  const loadMore = useCallback(() => {
    if (!cursor || loading || !trimmed) return;
    const current = ++requestId.current;
    setLoading(true);
    apiSearchSessions(trimmed, { limit: SEARCH_PAGE_SIZE, cursor })
      .then((page) => {
        if (requestId.current !== current) return;
        setItems((previous) => [...previous, ...page.items.map(toSearched)]);
        setTotalCount(page.total_count);
        setCursor(page.next_cursor ?? null);
      })
      .catch((e) => {
        // 이미 보여준 결과는 남긴다 — 다음 페이지를 못 받았다고 앞 페이지까지 지우지 않는다.
        if (requestId.current === current) setError(messageOf(e, '더 불러오지 못했어요'));
      })
      .finally(() => {
        if (requestId.current === current) setLoading(false);
      });
  }, [cursor, loading, trimmed]);

  if (!trimmed) {
    return {
      items: [],
      totalCount: 0,
      loading: false,
      error: null,
      hasMore: false,
      loadMore,
    };
  }
  return { items, totalCount, loading, error, hasMore: cursor !== null, loadMore };
}

/** 모드별로 묶은 목록 — 각 모드 안에서는 최근 대화가 위로 온다. */
export function useChatGroups(): { mode: ChatMode; label: string; tint: string; sessions: ChatSession[] }[] {
  const all = useChatSessions();
  return CHAT_MODE_ORDER.map((mode) => ({
    mode,
    ...CHAT_MODE_META[mode],
    sessions: sortByRecent(all.filter((s) => s.mode === mode)),
  }));
}
