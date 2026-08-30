import type { ApiRecommendationItem, ApiRenderJob } from '@/lib/recommendApi';
import type {
  ApiPersonaAction,
  ApiPersonaResult,
  ApiPersonaStatus,
  ApiStylistCatalog,
  ApiStylistRun,
  ApiResponseMode,
  StylistId,
} from '@/lib/stylistApi';

/**
 * 스타일리스트 API 가 없는 서버에서 화면을 그리기 위한 **목업**.
 *
 * 왜 필요한가 — 스타일리스트 라우트는 origin/feature/chat-main-integration 에만 있고
 * 배포 서버·main 에는 없다. 그래서 실행 검증이 불가능한데, 화면(카드 자리 잡기·로딩 채우기·
 * 실패 줄·다른 추천)은 지금 만들어 둬야 한다. 여기서 서버 흉내를 내고, 브랜치가 머지되면
 * lib/stylistApi.ts 가 첫 호출 성공과 함께 이쪽을 더 이상 부르지 않는다.
 *
 * 지키려는 것 — **모양과 순서는 진짜와 같게** 둔다.
 *   - run 을 만들면 인원수만큼 자리가 먼저 생기고(PENDING) 하나씩 끝난다.
 *   - 상태값은 PENDING/RUNNING/SUCCEEDED/FAILED 다 (COMPLETED 가 아니다).
 *   - display_order 로 순서가 고정된다. 끝난 순서로 자리가 바뀌지 않는다.
 *   - 근거 코드는 백엔드가 실제로 쓰는 값을 그대로 쓴다(*_stylist_strategy.py 의 _REASON_CODES).
 * 지어내지 않는 것 — 이미지. image_ref 를 빈 값으로 둬서 자리표시자가 뜨게 한다.
 * 아무 사진이나 걸면 "추천이 사진까지 만들어 냈다"고 잘못 읽힌다.
 */

/* ── 카탈로그 (api/apps/chat/config/stylist_personas.json 그대로) ── */

const CATALOG: ApiStylistCatalog = {
  schema_version: 'stylist-personas-v1',
  min_select: 1,
  max_select: 3,
  default_persona_ids: ['minimal'],
  last_selected_persona_ids: [],
  stylists: [
    {
      id: 'minimal',
      display_name: '미니멀',
      description: '색상과 실루엣을 정돈하고 반복 활용도가 높은 코디를 제안합니다.',
      display_order: 1,
    },
    {
      id: 'experimental',
      display_name: '모험',
      description: '최근 추천과 다른 관계를 탐색해 부담 없는 변화를 제안합니다.',
      display_order: 2,
    },
    {
      id: 'practical',
      display_name: '데일리',
      description: '날씨와 활동성, 관리 편의를 고려해 실제로 입기 좋은 코디를 제안합니다.',
      display_order: 3,
    },
  ],
};

const ORDER = new Map(CATALOG.stylists.map((s) => [s.id, s.display_order]));
const NAME = new Map(CATALOG.stylists.map((s) => [s.id, s.display_name]));

/* ── 코디 견본 ──
   페르소나마다 두 벌씩 둔다. '다른 추천'을 누르면 다음 벌로 넘어간다.
   문장은 근거(원인 → 효과)를 정확히 말하는 선까지만 쓴다 — 핏 때문인 것을 색 때문이라고
   바꿔 말하지 않는다. */

type Sample = {
  message: string;
  reasonCodes: string[];
  warnings: string[];
  items: { slot: string; name: string; category: string; color: string; price: number | null }[];
};

const SAMPLES: Record<StylistId, Sample[]> = {
  minimal: [
    {
      message: '색을 두 가지로 줄여서 출근 자리에서 차분하게 보이도록 맞췄어요.',
      reasonCodes: ['MINIMAL_COLOR_COHESION', 'MINIMAL_TPO_FIT', 'MINIMAL_WARDROBE_REUSABILITY'],
      warnings: [],
      items: [
        { slot: 'TOP', name: '라운드넥 니트', category: '니트', color: '차콜', price: null },
        { slot: 'BOTTOM', name: '스트레이트 슬랙스', category: '슬랙스', color: '차콜', price: null },
        { slot: 'OUTER', name: '싱글 코트', category: '코트', color: '오트밀', price: 189000 },
        { slot: 'SHOES', name: '플레인 더비', category: '구두', color: '블랙', price: null },
      ],
    },
    {
      message: '상의를 셔츠로 바꿔 목선을 정리하고, 색은 그대로 두 가지로 유지했어요.',
      reasonCodes: ['MINIMAL_SILHOUETTE_CONSISTENCY', 'MINIMAL_VISUAL_SIMPLICITY', 'MINIMAL_COLOR_COHESION'],
      warnings: [],
      items: [
        { slot: 'TOP', name: '옥스퍼드 셔츠', category: '셔츠', color: '화이트', price: null },
        { slot: 'BOTTOM', name: '테이퍼드 슬랙스', category: '슬랙스', color: '차콜', price: null },
        { slot: 'OUTER', name: '울 블레이저', category: '재킷', color: '차콜', price: 156000 },
        { slot: 'SHOES', name: '로퍼', category: '구두', color: '블랙', price: null },
      ],
    },
  ],
  experimental: [
    {
      message: '익숙한 니트는 그대로 두고 하의 실루엣만 바꿔 평소와 다른 비율을 만들었어요.',
      reasonCodes: ['EXPERIMENTAL_NOVELTY', 'EXPERIMENTAL_HISTORY_DISTANCE', 'EXPERIMENTAL_UNDERUSED_ITEM'],
      warnings: [],
      items: [
        { slot: 'TOP', name: '라운드넥 니트', category: '니트', color: '차콜', price: null },
        { slot: 'BOTTOM', name: '와이드 데님', category: '데님', color: '인디고', price: 78000 },
        { slot: 'OUTER', name: '스웨이드 블루종', category: '점퍼', color: '카멜', price: 214000 },
        { slot: 'SHOES', name: '스웨이드 로퍼', category: '구두', color: '다크브라운', price: null },
      ],
    },
    {
      message: '한동안 안 꺼낸 카디건을 겉옷 자리에 놓아 어깨선을 부드럽게 바꿨어요.',
      reasonCodes: ['EXPERIMENTAL_UNDERUSED_ITEM', 'EXPERIMENTAL_CROSS_STYLE', 'EXPERIMENTAL_RECENT_HISTORY'],
      warnings: ['최근 추천과 겉옷 종류가 겹치지 않게 골랐어요.'],
      items: [
        { slot: 'TOP', name: '스트라이프 티셔츠', category: '티셔츠', color: '아이보리', price: null },
        { slot: 'BOTTOM', name: '와이드 데님', category: '데님', color: '인디고', price: 78000 },
        { slot: 'OUTER', name: '오버핏 카디건', category: '가디건', color: '모스그린', price: null },
        { slot: 'SHOES', name: '레더 스니커즈', category: '스니커즈', color: '화이트', price: 119000 },
      ],
    },
  ],
  practical: [
    {
      message: '비 예보가 있어 젖으면 마르기 더딘 스웨이드는 빼고, 걸치기 쉬운 아우터로 골랐어요.',
      reasonCodes: ['PRACTICAL_WEATHER_FIT', 'PRACTICAL_WEARING_CONVENIENCE', 'PRACTICAL_MAINTENANCE_EASE'],
      warnings: [],
      items: [
        { slot: 'TOP', name: '기모 맨투맨', category: '맨투맨', color: '그레이', price: null },
        { slot: 'BOTTOM', name: '스트레이트 데님', category: '데님', color: '인디고', price: null },
        { slot: 'OUTER', name: '숏 패딩', category: '패딩', color: '블랙', price: 168000 },
        { slot: 'SHOES', name: '방수 첼시부츠', category: '부츠', color: '블랙', price: 132000 },
      ],
    },
    {
      message: '이동이 길어 벗고 입기 쉬운 집업으로 바꾸고, 신발은 젖어도 되는 것으로 뒀어요.',
      reasonCodes: ['PRACTICAL_ACTIVITY_FIT', 'PRACTICAL_WEARING_CONVENIENCE', 'PRACTICAL_WARDROBE_BUDGET_EFFICIENCY'],
      warnings: [],
      items: [
        { slot: 'TOP', name: '베이직 롱슬리브', category: '티셔츠', color: '차콜', price: null },
        { slot: 'BOTTOM', name: '스트레이트 데님', category: '데님', color: '인디고', price: null },
        { slot: 'OUTER', name: '플리스 집업', category: '집업', color: '크림', price: 89000 },
        { slot: 'SHOES', name: '방수 첼시부츠', category: '부츠', color: '블랙', price: 132000 },
      ],
    },
  ],
};

/** 모르는 페르소나(서버가 늘렸을 때)는 미니멀 견본으로 대신한다. */
function sampleFor(personaId: StylistId, variant: number): Sample {
  const list = SAMPLES[personaId] ?? SAMPLES.minimal;
  return list[variant % list.length];
}

/* ── 세션 상태 ─────────────────────────────────────── */

const sessions = new Map<string, { mode: ApiResponseMode; ids: StylistId[] }>();
/** 회원 마지막 선택값 — 서버의 '복원' 순서를 흉내 내기 위한 것. */
let lastSelected: StylistId[] = [];

/* ── run 상태 ──────────────────────────────────────── */

type MockResult = {
  personaId: StylistId;
  /** 이 시각이 지나면 끝난 것으로 본다 (폴링 때 계산한다). */
  dueAt: number;
  /** RUNNING 으로 바뀌는 시각 */
  runningAt: number;
  outcome: 'SUCCEEDED' | 'FAILED';
  variant: number;
  generation: number;
  retryCount: number;
  altCount: number;
  /** 0 이면 '다른 추천'을 요청한 적이 없다(IDLE). */
  altDueAt: number;
  /** 이미 갈아 끼운 '다른 추천'의 횟수. altCount 를 따라잡으면 교체가 끝난 것이다. */
  altApplied: number;
  resultId: string;
  cardId: string;
  saved: boolean;
};

type MockRun = { runId: string; results: MockResult[] };

const runs = new Map<string, MockRun>();

let seq = 0;
const nextId = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${++seq}`;

/** 스타일리스트 한 명이 답을 내놓기까지. 순서대로 조금씩 늦게 끝나 카드가 하나씩 채워진다. */
const FIRST_MS = 1400;
const STAGGER_MS = 1100;
const RUNNING_MS = 350;

/**
 * 실패 화면을 눈으로 확인할 방법이 없어서 두는 문. 질문에 '실패테스트'가 들어 있으면
 * 마지막 스타일리스트 한 명이 실패한다 — "한 명이 실패해도 나머지는 보인다"와 재실행 버튼을
 * 실제로 눌러 볼 수 있다. (실서버에는 없는 동작이고, 목업일 때만 열린다.)
 */
const FAIL_KEYWORD = '실패테스트';

function ensureRun(runId: string, personaIds: StylistId[], question: string): MockRun {
  const found = runs.get(runId);
  if (found) return found;

  const ordered = [...personaIds].sort((a, b) => (ORDER.get(a) ?? 99) - (ORDER.get(b) ?? 99));
  const failLast = question.includes(FAIL_KEYWORD);
  const now = Date.now();
  const run: MockRun = {
    runId,
    results: ordered.map((personaId, i) => ({
      personaId,
      runningAt: now + RUNNING_MS,
      dueAt: now + FIRST_MS + i * STAGGER_MS,
      outcome: failLast && i === ordered.length - 1 ? 'FAILED' : 'SUCCEEDED',
      variant: 0,
      generation: 1,
      retryCount: 0,
      altCount: 0,
      altDueAt: 0,
      altApplied: 0,
      resultId: nextId('mockresult'),
      cardId: nextId('mockcard'),
      saved: false,
    })),
  };
  runs.set(runId, run);
  return run;
}

function statusOf(r: MockResult, now: number): ApiPersonaStatus {
  if (now >= r.dueAt) return r.outcome;
  if (now >= r.runningAt) return 'RUNNING';
  return 'PENDING';
}

/**
 * 시각이 지나 확정된 변화를 실제로 반영한다. **폴링 때마다 한 번씩** 부른다.
 * 요청한 순간에 바로 바꾸면 카드가 눈앞에서 갈리고, 타이머로 바꾸면 폴링과 어긋난다 —
 * 읽는 시점에 시각을 보고 딱 한 번 갈아 끼우는 쪽이 어느 쪽 틈도 만들지 않는다.
 */
function settle(run: MockRun, now: number) {
  for (const r of run.results) {
    if (r.altApplied < r.altCount && r.altDueAt > 0 && now >= r.altDueAt) {
      r.altApplied = r.altCount;
      r.variant += 1;
      r.generation += 1;
      r.cardId = nextId('mockcard');
      r.resultId = nextId('mockresult');
      r.saved = false; // 다른 코디가 됐으니 저장 표시도 따라 풀린다
    }
  }
}

function toItems(sample: Sample, cardId: string): ApiRecommendationItem[] {
  return sample.items.map((it, i) => ({
    item_id: `${cardId}-i${i}`,
    position: i,
    slot: it.slot,
    source_type: it.price === null ? 'WARDROBE' : 'PRODUCT',
    // 목업엔 카탈로그가 없다 — 실제 응답에는 카탈로그 식별자가 온다.
    source_id: '',
    display_name: it.name,
    category: it.category,
    color: it.color,
    // 목업은 사진을 지어내지 않는다 — 빈 값이면 화면이 자리표시자를 그린다.
    image_ref: '',
    price_snapshot: it.price,
    purchase_url: null,
    reasons: [],
    note: '',
  }));
}

function toResult(r: MockResult, now: number): ApiPersonaResult {
  const status = statusOf(r, now);
  const sample = sampleFor(r.personaId, r.variant);
  const done = status === 'SUCCEEDED';
  const altRunning = r.altDueAt > 0 && now < r.altDueAt;
  const total = sample.items.reduce((sum, it) => sum + (it.price ?? 0), 0);

  return {
    persona_id: r.personaId,
    display_name: NAME.get(r.personaId) ?? r.personaId,
    display_order: ORDER.get(r.personaId) ?? 99,
    status,
    result_id: done ? r.resultId : null,
    result_type: done ? (r.generation > 1 ? 'ALTERNATIVE' : 'INITIAL') : null,
    generation: done ? r.generation : null,
    previous_result_ids: [],
    message: done ? sample.message : '',
    validated_reason_codes: done ? sample.reasonCodes : [],
    card: done
      ? {
          card_id: r.cardId,
          rank: 1,
          total_product_price: total > 0 ? total : null,
          validation_reasons: [],
          warnings: sample.warnings,
          items: toItems(sample, r.cardId),
          image: null,
          is_saved: r.saved,
        }
      : null,
    error:
      status === 'FAILED'
        ? {
            code: 'PERSONA_CANDIDATE_SHORTAGE',
            message: '조건에 맞는 후보가 부족해 이 관점은 코디를 만들지 못했어요.',
          }
        : null,
    retry_count: r.retryCount,
    alternative_status: r.altDueAt === 0 ? 'IDLE' : altRunning ? 'RUNNING' : 'SUCCEEDED',
    alternative_count: r.altCount,
    alternative_error_code: '',
    alternative_error_message: '',
    latency_ms: done ? Math.max(0, r.dueAt - r.runningAt) : 0,
    started_at: null,
    completed_at: null,
  };
}

function toRun(run: MockRun, now: number): ApiStylistRun {
  settle(run, now);
  const results = run.results.map((r) => toResult(r, now));
  const allDone = results.every((r) => r.status === 'SUCCEEDED' || r.status === 'FAILED');
  const anyDone = results.some((r) => r.status !== 'PENDING');
  return {
    id: run.runId,
    session_id: '',
    request_message_id: '',
    response_message_id: null,
    status: allDone ? 'SUCCEEDED' : anyDone ? 'RUNNING' : 'PENDING',
    response_mode: 'STYLIST',
    persona_ids: run.results.map((r) => r.personaId),
    results,
    error_code: '',
    error_message: '',
    created_at: new Date(now).toISOString(),
    updated_at: new Date(now).toISOString(),
  };
}

function actionOn(runId: string, personaId: StylistId, apply: (r: MockResult) => void) {
  const run = runs.get(runId);
  if (!run) throw new Error('이 추천을 찾지 못했어요');
  const target = run.results.find((r) => r.personaId === personaId);
  if (!target) throw new Error('이 스타일리스트를 찾지 못했어요');
  apply(target);
  return { run: toRun(run, Date.now()), events_url: '' } satisfies ApiPersonaAction;
}

/* ── 목업 서버 ─────────────────────────────────────── */

export const stylistMock = {
  async listStylists(): Promise<ApiStylistCatalog> {
    return { ...CATALOG, last_selected_persona_ids: lastSelected };
  },

  /** 선택값을 생략하면 서버와 같은 순서로 복원한다: 세션 이전값 → 회원 마지막값 → minimal. */
  async updateResponseMode(
    sessionId: string,
    mode: ApiResponseMode,
    personaIds?: StylistId[],
  ): Promise<{ response_mode: ApiResponseMode; selected_persona_ids: StylistId[] }> {
    const before = sessions.get(sessionId);
    const restored =
      personaIds ??
      (before?.ids.length ? before.ids : lastSelected.length ? lastSelected : CATALOG.default_persona_ids);
    const ids = mode === 'STYLIST' ? restored : (before?.ids ?? restored);
    sessions.set(sessionId, { mode, ids });
    // 끌 때도 선택값은 지우지 않는다 — 다시 켜면 복원돼야 한다.
    if (mode === 'STYLIST') lastSelected = ids;
    return { response_mode: mode, selected_persona_ids: ids };
  },

  /**
   * run 조회. 처음 보는 run 이면 hint 로 자리를 만든다 —
   * 실제 run 은 진짜 서버가 만들었고(대화는 살아 있다) 페르소나 분기만 여기서 흉내 내는 것이다.
   */
  async getRun(
    runId: string,
    hint?: { personaIds: StylistId[]; question: string },
  ): Promise<ApiStylistRun> {
    const run = runs.get(runId) ?? ensureRun(runId, hint?.personaIds ?? ['minimal'], hint?.question ?? '');
    return toRun(run, Date.now());
  },

  async retryPersona(runId: string, personaId: StylistId): Promise<ApiPersonaAction> {
    return actionOn(runId, personaId, (r) => {
      const now = Date.now();
      r.retryCount += 1;
      r.runningAt = now;
      r.dueAt = now + FIRST_MS;
      r.outcome = 'SUCCEEDED'; // 재실행은 성공하는 쪽으로 둔다 — 막다른 길을 만들지 않으려고.
    });
  },

  async requestAlternative(runId: string, personaId: StylistId): Promise<ApiPersonaAction> {
    return actionOn(runId, personaId, (r) => {
      r.altCount += 1;
      r.altDueAt = Date.now() + FIRST_MS;
      /* 지금 카드는 그대로 두고, 시간이 지나면 새 카드가 대신 들어온다.
         교체를 타이머로 하지 않는 이유 — 타이머와 폴링이 같은 순간에 걸리면 '다른 추천 완료'
         상태인데 카드는 아직 옛것인 한 프레임이 생긴다. settle() 이 폴링 때마다 시각을 보고
         한 번만 갈아 끼우므로 그 틈이 없다. */
    });
  },

  async saveCard(resultId: string, cardId: string): Promise<unknown> {
    for (const run of runs.values()) {
      for (const r of run.results) {
        if (r.resultId === resultId && r.cardId === cardId) r.saved = true;
      }
    }
    return { saved: true };
  },

  async renderCard(_resultId: string, cardId: string): Promise<ApiRenderJob> {
    const now = new Date().toISOString();
    return {
      job_id: `mock-render-${cardId}`,
      card_id: cardId,
      status: 'FAILED',
      cache_hit: false,
      image_url: null,
      error: {
        code: 'STYLIST_MOCK_RENDER_UNAVAILABLE',
        message: '예시 카드에서는 코디 이미지를 생성하지 않아요.',
      },
      created_at: now,
      updated_at: now,
    };
  },

  async getCardRender(resultId: string, cardId: string): Promise<ApiRenderJob> {
    return this.renderCard(resultId, cardId);
  },
};
