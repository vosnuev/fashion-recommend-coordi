/**
 * 신체치수 10개의 표시 규칙과 '재는 법' 안내 원문.
 *
 * 백엔드(BodyMeasurement · PATCH /users/me/body/detail/)가 다루는 상세 항목은
 * **둘레·너비 7개 + 체형 지표 3개 = 10개**다. 키 이름은 API 필드명과 1:1로 맞춰
 * 그대로 PATCH 본문에 넣을 수 있게 했다 (신규 3개만 snake_case인 이유).
 *
 * 값의 성격이 둘로 갈린다.
 *   - size       : cm 단위 실측치. 사용자가 줄자로 직접 재서 고칠 수 있다.
 *   - proportion : 체형 분류용 길이·비율. 넥라인·기장·하의 실루엣 추천에 쓰인다.
 *
 * 안내 문구는 golden-set/body/docs/02-body-proportion-rules.md 를 근거로 한다.
 * 특히 shoulder 는 사이즈코리아 '어깨사이너비'(정면 직선, 평균 37.3cm)이고
 * '어깨사이길이'(등을 돌아 재는 42.2cm)가 아니다 — 사용자가 가장 많이 틀리는 지점이라
 * 안내에 경고를 따로 뒀다.
 */

export type BodyMeasureKey =
  | 'shoulder'
  | 'chest'
  | 'waist'
  | 'hip'
  | 'thigh'
  | 'calf'
  | 'arm'
  | 'neck_length'
  | 'thigh_calf_ratio'
  | 'torso_leg_ratio';

export type BodyMeasureSpec = {
  key: BodyMeasureKey;
  label: string;
  /** 타일처럼 좁은 자리에 쓰는 짧은 이름. 없으면 label 을 쓴다 */
  shortLabel?: string;
  /** 'size' = cm 실측치(7개), 'proportion' = 체형 지표(3개) */
  group: 'size' | 'proportion';
  /** 값 옆에 붙는 단위. 비율은 단위가 없다 */
  unit: 'cm' | null;
  /** 표시·입력 소수 자릿수 (cm 1자리 · 비율 3자리 — 백엔드 Decimal 자릿수와 동일) */
  decimals: 1 | 3;
  /**
   * 사용자가 고칠 수 있는가.
   *
   * 줄자로 잴 수 있는 값만 true 다. 비율 2개는 **서버가 길이에서 계산해 주는 파생값**이라
   * false — 사람이 "0.774"를 잴 방법이 없고, 고쳐 봐야 서버의 길이 값과 어긋난 채 저장됐다가
   * 다음 추정 때 덮어써진다. 읽기 전용으로 두고 저장 본문에서도 뺀다.
   */
  editable: boolean;
  /** 입력 허용 범위 (백엔드 validator 와 동일). 벗어나면 저장이 400 이 된다 */
  min: number;
  max: number;
  /** 목록에서 라벨 밑에 붙는 한 줄. 없으면 안 그린다 */
  caption?: string;
  /** 가이드 시트 — 한 문장 정의 */
  summary: string;
  /** 가이드 시트 — 재는 순서 */
  steps: string[];
  /** 가이드 시트 — 틀리기 쉬운 지점 (있을 때만) */
  caution?: string;
};

export const BODY_MEASURES: readonly BodyMeasureSpec[] = [
  {
    key: 'shoulder',
    label: '어깨너비',
    group: 'size',
    unit: 'cm',
    decimals: 1,
    editable: true,
    min: 1,
    max: 999.9,
    summary: '양쪽 어깨 끝점을 잇는 정면 직선 거리예요.',
    steps: [
      '팔에 힘을 빼고 거울 앞에 똑바로 섭니다.',
      '어깨에서 가장 바깥쪽으로 튀어나온 뼈(어깨끝점)를 양쪽 다 짚습니다.',
      '두 점 사이를 자로 곧게 잽니다. 등을 돌아가지 않게 합니다.',
    ],
    caution:
      '등을 돌아 체표면을 따라 재면 4~5cm 더 나옵니다. 정면 직선 기준이며, 사이즈코리아 8차 실측 성인 평균은 약 37cm입니다.',
  },
  {
    key: 'chest',
    label: '가슴둘레',
    group: 'size',
    unit: 'cm',
    decimals: 1,
    editable: true,
    min: 1,
    max: 999.9,
    summary: '가슴에서 가장 두꺼운 곳을 한 바퀴 두른 둘레예요.',
    steps: [
      '숨을 자연스럽게 내쉰 상태로 섭니다.',
      '가슴에서 가장 나온 지점에 줄자를 겁니다.',
      '바닥과 수평이 되게 등 뒤로 한 바퀴 돌립니다.',
    ],
    caution: '줄자가 등 쪽에서 내려가면 실제보다 크게 나옵니다.',
  },
  {
    key: 'waist',
    label: '허리둘레',
    group: 'size',
    unit: 'cm',
    decimals: 1,
    editable: true,
    min: 1,
    max: 999.9,
    summary: '갈비뼈 아래와 골반 위 사이, 가장 가는 곳의 둘레예요.',
    steps: [
      '배에 힘을 주지 않고 편하게 섭니다.',
      '허리에서 가장 잘록한 지점을 찾습니다.',
      '바닥과 수평으로 한 바퀴 돌립니다.',
    ],
    caution: '바지를 입는 위치(골반)가 아니라 가장 가는 곳 기준입니다.',
  },
  {
    key: 'hip',
    label: '엉덩이둘레',
    group: 'size',
    unit: 'cm',
    decimals: 1,
    editable: true,
    min: 1,
    max: 999.9,
    summary: '엉덩이에서 가장 튀어나온 곳의 둘레예요.',
    steps: [
      '두 발을 모으고 섭니다.',
      '옆에서 봤을 때 가장 나온 지점에 줄자를 겁니다.',
      '바닥과 수평으로 한 바퀴 돌립니다.',
    ],
  },
  {
    key: 'thigh',
    label: '허벅지둘레',
    group: 'size',
    unit: 'cm',
    decimals: 1,
    editable: true,
    min: 1,
    max: 999.9,
    summary: '허벅지에서 가장 굵은 곳의 둘레예요.',
    steps: [
      '두 발을 어깨너비로 벌리고 체중을 양쪽에 고르게 싣습니다.',
      '사타구니 바로 아래, 가장 굵은 지점을 찾습니다.',
      '바닥과 수평으로 한 바퀴 돌립니다.',
    ],
  },
  {
    key: 'calf',
    label: '종아리둘레',
    group: 'size',
    unit: 'cm',
    decimals: 1,
    editable: true,
    min: 1,
    max: 999.9,
    summary: '종아리에서 가장 굵은 곳의 둘레예요.',
    steps: [
      '맨발로 서서 체중을 양발에 고르게 싣습니다.',
      '종아리 알이 가장 도드라지는 지점을 찾습니다.',
      '바닥과 수평으로 한 바퀴 돌립니다.',
    ],
  },
  {
    key: 'arm',
    label: '팔뚝둘레',
    group: 'size',
    unit: 'cm',
    decimals: 1,
    editable: true,
    min: 1,
    max: 999.9,
    summary: '어깨와 팔꿈치 사이, 위팔에서 가장 굵은 곳의 둘레예요.',
    steps: [
      '팔을 자연스럽게 내리고 힘을 뺍니다.',
      '어깨와 팔꿈치의 중간쯤에서 가장 굵은 지점을 찾습니다.',
      '팔 축과 직각이 되게 한 바퀴 돌립니다.',
    ],
    caution: '팔에 힘을 주면(알통) 실제보다 크게 나옵니다.',
  },
  {
    key: 'neck_length',
    label: '목길이',
    group: 'proportion',
    unit: 'cm',
    decimals: 1,
    editable: true,
    min: 1,
    max: 999.9,
    caption: '넥라인 추천에 쓰여요 (짧으면 V넥·U넥, 길면 하이넥·터틀넥)',
    summary: '턱선 아래부터 목앞·쇄골선까지의 세로 길이예요.',
    steps: [
      '정면을 보고 턱을 당기지 않은 자연스러운 자세로 섭니다.',
      '턱뼈 아래 지점에서 목앞·쇄골선까지의 세로 거리를 잽니다.',
    ],
    caution: '둘레가 아니라 세로 길이입니다. 목둘레와 다릅니다.',
  },
  {
    key: 'thigh_calf_ratio',
    label: '허벅지:종아리 비율',
    shortLabel: '허벅지:종아리',
    group: 'proportion',
    unit: null,
    decimals: 3,
    editable: false,
    min: 0.7,
    max: 1.3,
    caption: '하의 실루엣 추천에 쓰여요 (0.7 ~ 1.3)',
    summary: '샅선에서 무릎까지 길이 ÷ 무릎에서 복사뼈까지 길이예요.',
    steps: [
      '옆에서 봤을 때 샅선·인심 지점에서 무릎 가운데까지를 잽니다.',
      '무릎 가운데에서 복사뼈까지를 잽니다.',
      '앞의 값을 뒤의 값으로 나눕니다.',
    ],
    caution:
      '둘레가 아니라 길이의 비율입니다. 값이 클수록 무릎이 아래쪽에 있어 와이드·부츠컷이 잘 맞습니다.',
  },
  {
    key: 'torso_leg_ratio',
    label: '상하체 비율',
    group: 'proportion',
    unit: null,
    decimals: 3,
    editable: false,
    min: 0.45,
    max: 0.7,
    caption: '상의 기장·밑위 추천에 쓰여요 (약 0.45 ~ 0.70)',
    summary: '어깨에서 골반까지 길이 ÷ 골반에서 발목까지 길이예요.',
    steps: [
      '어깨선에서 골반 바깥쪽 지점까지를 잽니다.',
      '골반점에서 복사뼈/발목까지를 잽니다.',
      '앞의 값을 뒤의 값으로 나눕니다.',
    ],
    caution:
      '무사진은 정확 랜드마크 Hist v2의 추정값이며, 사진 측정 결과와 함께 사용자가 확인할 수 있습니다.',
  },
] as const;

/**
 * 접혀 있을 때 보여줄 개수 — 어깨·가슴·허리·엉덩이.
 *
 * 10개를 한 번에 펼치면 결과 화면이 숫자 벽이 되고, 정작 옷 사이즈를 정하는
 * 네 값이 묻힌다. 나머지 6개는 '더보기'로 미룬다 (순서는 BODY_MEASURES 정의 순).
 */
export const PREVIEW_COUNT = 4;

/** 타일 등 좁은 자리에 쓸 이름 */
export const measureLabel = (spec: BodyMeasureSpec): string => spec.shortLabel ?? spec.label;

export const BODY_MEASURE_BY_KEY: Record<BodyMeasureKey, BodyMeasureSpec> =
  Object.fromEntries(BODY_MEASURES.map((m) => [m.key, m])) as Record<
    BodyMeasureKey,
    BodyMeasureSpec
  >;

/** 사용자가 고칠 수 있는 항목 — 저장(PATCH detail) 본문도 이 목록으로 만든다 */
export const EDITABLE_MEASURES = BODY_MEASURES.filter((m) => m.editable);
