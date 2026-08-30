import Svg, {
  Circle,
  Ellipse,
  G,
  Line,
  Path,
  Polyline,
  Text as SvgText,
} from 'react-native-svg';

import { BODY_MEASURES, type BodyMeasureKey } from '@/constants/body-measures';
import { Editorial, ink } from '@/constants/theme';

/**
 * 정면 인체 도식 — 10개 측정 위치를 **한 그림에 전부** 표시한다.
 *
 * 항목을 하나씩 넘겨 보게 하면 "내 어깨가 어디서 어디까지인지"는 알아도 다른 치수와의
 * 관계가 안 보인다. 한 몸에 다 얹으면 위아래 순서와 간격이 한눈에 잡히고, 넘기는 동작도 사라진다.
 *
 * 이름을 그림 안에 다 쓰면 한글 라벨이 서로 겹쳐 못 읽는다. 그래서 그림에는 **번호만** 찍고
 * 이름·설명은 밖의 목록(measure-guide-sheet)이 맡는다 — 번호는 BODY_MEASURES 정의 순서와 같다.
 *
 * 표시 종류는 넷이다.
 *   width   — 두 점 사이 직선 너비 (어깨너비). 양 끝 점이 '어디까지'를 못 박는다.
 *   girth   — 몸을 감는 둘레. 점선 타원이라 뒤로 돌아가는 느낌이 난다.
 *   length  — 두 높이 사이 세로 길이 (목길이).
 *   spans   — 구간 대 구간의 비율. 눈금 사이가 각 구간이다.
 *
 * 좌표계는 viewBox 200×380 고정이고 아래 상수가 인체·표시의 단일 출처다.
 */

// 인체 기준선 — 표시 좌표는 전부 여기서 끌어다 쓴다.
const SHOULDER_Y = 88;
const SHOULDER_L = 62;
const SHOULDER_R = 138;
const HIP_Y = 214;
const KNEE_Y = 290;
const ANKLE_Y = 356;

/** 몸통 실루엣 — 어깨 → 허리(잘록) → 골반 */
const TORSO_PATH = [
  `M ${SHOULDER_L},${SHOULDER_Y}`,
  'C 66,120 72,142 76,164',
  'C 70,184 68,198 70,214',
  `L 130,${HIP_Y}`,
  'C 132,198 130,184 124,164',
  `C 128,142 134,120 ${SHOULDER_R},${SHOULDER_Y}`,
  'Z',
].join(' ');

/** 목 — 아래쪽을 막지 않는다. 가로선을 그으면 몸통 위에 상자가 얹힌 것처럼 보인다 */
const NECK_PATH = 'M 92,50 L 92,90 M 108,50 L 108,90';

const ARM_L = '66,92 54,140 58,196';
const ARM_R = '134,92 146,140 142,196';
const LEG_L = `86,${HIP_Y} 84,${KNEE_Y} 88,${ANKLE_Y}`;
const LEG_R = `114,${HIP_Y} 116,${KNEE_Y} 112,${ANKLE_Y}`;

const FIGURE_LINE = ink(0.22);
const FIGURE_FILL = ink(0.07);
const MARK = Editorial.ink;

type Mark =
  | { kind: 'width'; y: number; x1: number; x2: number; badge: [number, number] }
  | {
      kind: 'girth';
      cx: number;
      cy: number;
      rx: number;
      ry: number;
      tilt?: number;
      badge: [number, number];
    }
  | { kind: 'length'; x: number; y1: number; y2: number; badge: [number, number] }
  | { kind: 'spans'; x: number; ticks: number[]; badge: [number, number] };

/**
 * 항목별 표시와 번호 위치. girth 의 rx 는 그 높이에서의 몸 반폭 + 여유(2~3)로 잡아
 * 줄자가 몸에 걸쳐 보이게 한다. badge 는 인체와 겹치지 않는 바깥 자리로 뺐다.
 */
const MARKS: Record<BodyMeasureKey, Mark> = {
  shoulder: { kind: 'width', y: SHOULDER_Y, x1: SHOULDER_L, x2: SHOULDER_R, badge: [44, 84] },
  chest: { kind: 'girth', cx: 100, cy: 120, rx: 36, ry: 7, badge: [40, 118] },
  waist: { kind: 'girth', cx: 100, cy: 164, rx: 27, ry: 6, badge: [36, 163] },
  hip: { kind: 'girth', cx: 100, cy: 206, rx: 33, ry: 7, badge: [48, 208] },
  thigh: { kind: 'girth', cx: 85, cy: 242, rx: 14, ry: 4.5, badge: [56, 242] },
  calf: { kind: 'girth', cx: 85, cy: 312, rx: 12, ry: 4, badge: [58, 312] },
  // 팔뚝은 오른팔에 건다 — 왼쪽은 둘레 번호가 몰려 있어 자리가 없다.
  arm: { kind: 'girth', cx: 143, cy: 126, rx: 10, ry: 3.5, tilt: 14, badge: [162, 122] },
  neck_length: { kind: 'length', x: 118, y1: 58, y2: SHOULDER_Y, badge: [130, 70] },
  torso_leg_ratio: {
    kind: 'spans',
    x: 152,
    ticks: [SHOULDER_Y, HIP_Y, ANKLE_Y],
    badge: [160, 150],
  },
  thigh_calf_ratio: {
    kind: 'spans',
    x: 176,
    ticks: [HIP_Y, KNEE_Y, ANKLE_Y],
    badge: [184, 300],
  },
};

/**
 * 번호를 그 표시에 이어 줄 점 — 둘레는 타원의 가까운 쪽 끝, 나머지는 선 위의 같은 높이.
 * 번호가 표시에서 떨어져 있으면 어느 표시의 번호인지 읽히지 않는다.
 */
function anchorOf(mark: Mark): [number, number] {
  const [bx, by] = mark.badge;
  if (mark.kind === 'width') return [bx < mark.x1 ? mark.x1 : mark.x2, mark.y];
  if (mark.kind === 'girth') {
    return [bx < mark.cx ? mark.cx - mark.rx : mark.cx + mark.rx, mark.cy];
  }
  if (mark.kind === 'length') {
    return [mark.x, Math.min(Math.max(by, mark.y1), mark.y2)];
  }
  const first = mark.ticks[0];
  const last = mark.ticks[mark.ticks.length - 1];
  return [mark.x, Math.min(Math.max(by, first), last)];
}

const BADGE_R = 8.5;

/** 그림 위 번호 — 목록의 같은 번호와 짝을 이룬다 */
function Badge({ mark, n, dim }: { mark: Mark; n: number; dim: boolean }) {
  const [x, y] = mark.badge;
  const [ax, ay] = anchorOf(mark);
  const dx = ax - x;
  const dy = ay - y;
  const dist = Math.hypot(dx, dy);
  // 번호 원 가장자리에서 표시까지만 잇는다. 붙어 있으면(원 안이면) 선을 안 그린다.
  const showLeader = dist > BADGE_R + 4;
  const sx = x + (dx / dist) * BADGE_R;
  const sy = y + (dy / dist) * BADGE_R;

  return (
    <G opacity={dim ? 0.32 : 1}>
      {showLeader ? (
        <Line x1={sx} y1={sy} x2={ax} y2={ay} stroke={MARK} strokeWidth={1} strokeDasharray="2 2" />
      ) : null}
      <Circle cx={x} cy={y} r={BADGE_R} fill={MARK} />
      <SvgText
        x={x}
        y={y + 3.4}
        fill={Editorial.white}
        fontSize={n >= 10 ? 8.5 : 10}
        fontWeight="700"
        textAnchor="middle">
        {n}
      </SvgText>
    </G>
  );
}

function MarkShape({ mark, dim }: { mark: Mark; dim: boolean }) {
  const stroke = MARK;
  const opacity = dim ? 0.3 : 1;
  const width = dim ? 2 : 2.5;

  if (mark.kind === 'width') {
    return (
      <G opacity={opacity}>
        <Line
          x1={mark.x1}
          y1={mark.y}
          x2={mark.x2}
          y2={mark.y}
          stroke={stroke}
          strokeWidth={width}
        />
        <Line
          x1={mark.x1}
          y1={mark.y - 8}
          x2={mark.x1}
          y2={mark.y + 8}
          stroke={stroke}
          strokeWidth={width}
        />
        <Line
          x1={mark.x2}
          y1={mark.y - 8}
          x2={mark.x2}
          y2={mark.y + 8}
          stroke={stroke}
          strokeWidth={width}
        />
        <Circle cx={mark.x1} cy={mark.y} r={4} fill={stroke} />
        <Circle cx={mark.x2} cy={mark.y} r={4} fill={stroke} />
      </G>
    );
  }

  if (mark.kind === 'girth') {
    return (
      <Ellipse
        cx={mark.cx}
        cy={mark.cy}
        rx={mark.rx}
        ry={mark.ry}
        fill="none"
        stroke={stroke}
        strokeWidth={width}
        strokeDasharray="5 4"
        opacity={opacity}
        origin={`${mark.cx}, ${mark.cy}`}
        rotation={mark.tilt ?? 0}
      />
    );
  }

  if (mark.kind === 'length') {
    return (
      <G opacity={opacity}>
        <Line x1={mark.x} y1={mark.y1} x2={mark.x} y2={mark.y2} stroke={stroke} strokeWidth={width} />
        <Line
          x1={mark.x - 6}
          y1={mark.y1}
          x2={mark.x + 6}
          y2={mark.y1}
          stroke={stroke}
          strokeWidth={width}
        />
        <Line
          x1={mark.x - 6}
          y1={mark.y2}
          x2={mark.x + 6}
          y2={mark.y2}
          stroke={stroke}
          strokeWidth={width}
        />
      </G>
    );
  }

  const [first, ...rest] = mark.ticks;
  const last = mark.ticks[mark.ticks.length - 1];
  return (
    <G opacity={opacity}>
      <Line x1={mark.x} y1={first} x2={mark.x} y2={last} stroke={stroke} strokeWidth={width} />
      {mark.ticks.map((y) => (
        <Line
          key={y}
          x1={mark.x - 6}
          y1={y}
          x2={mark.x + 6}
          y2={y}
          stroke={stroke}
          strokeWidth={width}
        />
      ))}
      {/* 가운데 눈금이 두 구간을 가르는 지점이라 조금 더 길게 그어 눈에 띄게 한다 */}
      {rest.length > 1 ? (
        <Line
          x1={mark.x - 10}
          y1={mark.ticks[1]}
          x2={mark.x + 10}
          y2={mark.ticks[1]}
          stroke={stroke}
          strokeWidth={width}
        />
      ) : null}
    </G>
  );
}

export function BodyFigureAll({
  highlight,
  width = 300,
}: {
  /** 이 항목만 진하게, 나머지는 흐리게. 없으면 전부 같은 세기 */
  highlight?: BodyMeasureKey | null;
  width?: number;
}) {
  return (
    <Svg width={width} height={width * (380 / 200)} viewBox="0 0 200 380">
      {/* ── 인체 ── 팔·다리를 몸통보다 먼저 그려 어깨·골반 이음매가 몸통에 덮이게 한다 */}
      {[ARM_L, ARM_R].map((points) => (
        <Polyline
          key={points}
          points={points}
          fill="none"
          stroke={FIGURE_LINE}
          strokeWidth={9}
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={0.5}
        />
      ))}
      {[LEG_L, LEG_R].map((points) => (
        <Polyline
          key={points}
          points={points}
          fill="none"
          stroke={FIGURE_LINE}
          strokeWidth={16}
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={0.5}
        />
      ))}
      <Path d={NECK_PATH} stroke={FIGURE_LINE} strokeWidth={2.5} fill="none" />
      <Path d={TORSO_PATH} fill={FIGURE_FILL} stroke={FIGURE_LINE} strokeWidth={2.5} />
      <Circle cx={100} cy={36} r={19} stroke={FIGURE_LINE} strokeWidth={2.5} fill={FIGURE_FILL} />

      {/* ── 표시 + 번호 ── */}
      {BODY_MEASURES.map((spec, i) => {
        const mark = MARKS[spec.key];
        const dim = Boolean(highlight) && highlight !== spec.key;
        return (
          <G key={spec.key}>
            <MarkShape mark={mark} dim={dim} />
            <Badge mark={mark} n={i + 1} dim={dim} />
          </G>
        );
      })}
    </Svg>
  );
}
