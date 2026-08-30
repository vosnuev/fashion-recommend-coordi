/**
 * 추구미 선호도 선택지.
 *
 * 팀원(vosnuev)이 설계한 pursuit/PursuitPreferenceScreen.tsx 의 카테고리·코드 체계를 그대로 옮긴 것.
 * 항목 아이콘(SVG)도 팀 설계의 것을 가져와 코드값에 매핑했다.
 * 백엔드가 이 목록을 내려주게 되면 이 파일을 API 응답으로 교체한다(코드값은 유지).
 */

import type { FC } from 'react';
import type { SvgProps } from 'react-native-svg';

// ── 항목 아이콘 (metro 의 svg-transformer 로 컴포넌트화) ──
// 넥라인
import RoundNeckIcon from '@/assets/icons/necklines/round-neck.svg';
import VNeckIcon from '@/assets/icons/necklines/v-neck.svg';
import UNeckIcon from '@/assets/icons/necklines/u-neck.svg';
import HoodIcon from '@/assets/icons/necklines/hood.svg';
import SquareNeckIcon from '@/assets/icons/necklines/square-neck.svg';
import OffShoulderIcon from '@/assets/icons/necklines/off-shoulder.svg';
import HalfHighNeckIcon from '@/assets/icons/necklines/half_high.svg';
import OneShoulderIcon from '@/assets/icons/necklines/one-shoulder.svg';
import HalterNeckIcon from '@/assets/icons/necklines/halter-neck.svg';
import BoatNeckIcon from '@/assets/icons/necklines/boat-neck.svg';
import SweetheartNeckIcon from '@/assets/icons/necklines/sweetheart-neck.svg';
import TurtleneckIcon from '@/assets/icons/necklines/turtleneck.svg';
import HighNeckIcon from '@/assets/icons/necklines/high-neck.svg';
import HalfZipIcon from '@/assets/icons/necklines/half-zip.svg';
// 소매
import LongSleeveIcon from '@/assets/icons/sleeve-length/long-sleeve.svg';
import ShortSleeveIcon from '@/assets/icons/sleeve-length/short-sleeve.svg';
import SleevelessIcon from '@/assets/icons/sleeve-length/sleeveless.svg';
import ThreeQuarterSleeveIcon from '@/assets/icons/sleeve-length/three-quarter-sleeve.svg';
// 상의 핏
import NormalFitIcon from '@/assets/icons/top-fit/normal-fit.svg';
import SlimFitIcon from '@/assets/icons/top-fit/slim-fit.svg';
import LooseFitIcon from '@/assets/icons/top-fit/loose-fit.svg';
import OverFitIcon from '@/assets/icons/top-fit/over-fit.svg';
// 상의 기장
import CropLengthIcon from '@/assets/icons/top-length/crop.svg';
import ShortLengthIcon from '@/assets/icons/top-length/short-length.svg';
import RegularLengthIcon from '@/assets/icons/top-length/regular-length.svg';
import LongLengthIcon from '@/assets/icons/top-length/long-length.svg';
// 바지 핏
import WidePantsIcon from '@/assets/icons/pants-fit/wide.svg';
import JoggerPantsIcon from '@/assets/icons/pants-fit/jogger.svg';
import StraightPantsIcon from '@/assets/icons/pants-fit/straight.svg';
import SkinnyPantsIcon from '@/assets/icons/pants-fit/skinny.svg';
import BootcutPantsIcon from '@/assets/icons/pants-fit/bootcut.svg';
import SlacksPantsIcon from '@/assets/icons/pants-fit/slacks.svg';
import SemiWidePantsIcon from '@/assets/icons/pants-fit/semi-wide.svg';
// 바지 기장
import ShortShortsIcon from '@/assets/icons/pants-length/short-shorts-3.svg';
import ShortsIcon from '@/assets/icons/pants-length/shorts-5.svg';
import SevenPartPantsIcon from '@/assets/icons/pants-length/three-quarter-pants.svg';
import LongPantsIcon from '@/assets/icons/pants-length/long-pants.svg';
// 스커트 길이
import MiniSkirtIcon from '@/assets/icons/skirt-length/mini.svg';
import MidiSkirtIcon from '@/assets/icons/skirt-length/midi.svg';
import LongSkirtIcon from '@/assets/icons/skirt-length/long-skirt.svg';
import MaxiSkirtIcon from '@/assets/icons/skirt-length/maxi.svg';
// 스커트 종류
import ALineSkirtIcon from '@/assets/icons/skirt-type/a-line.svg';
import PleatsSkirtIcon from '@/assets/icons/skirt-type/pleated.svg';
import FlareSkirtIcon from '@/assets/icons/skirt-type/flare.svg';
import HLineSkirtIcon from '@/assets/icons/skirt-type/h-line.svg';
import MermaidSkirtIcon from '@/assets/icons/skirt-type/mermaid.svg';
import BalloonSkirtIcon from '@/assets/icons/skirt-type/balloon.svg';

export type PreferenceMode = 'preferred' | 'avoided';

export type PreferenceOption = {
  code: string;
  label: string;
  /** 색상 카테고리에서만 사용 — 칩에 색 견본을 함께 보여준다. */
  colorHex?: string;
  /** 형태 카테고리(넥라인·소매·핏 등)에서만 사용 — 칩에 아이콘을 함께 보여준다. */
  icon?: FC<SvgProps>;
};

export type CategoryKey =
  | 'seasons'
  | 'styles'
  | 'colors'
  | 'pants_fits'
  | 'pants_lengths'
  | 'skirt_lengths'
  | 'skirt_types'
  | 'necklines'
  | 'sleeves'
  | 'top_fits'
  | 'top_lengths';

export const CATEGORY_TITLES: Record<CategoryKey, string> = {
  seasons: '계절',
  styles: '스타일',
  colors: '색상',
  pants_fits: '바지 핏',
  pants_lengths: '바지 기장',
  skirt_lengths: '스커트 길이',
  skirt_types: '스커트 종류',
  necklines: '넥라인',
  sleeves: '소매',
  top_fits: '상의 핏',
  top_lengths: '상의 기장',
};

/**
 * 카테고리를 나타내는 이모지 (그룹 헤더용).
 * 개별 항목 구분은 위 SVG 아이콘이 담당하므로, 여기엔 대표 카테고리에만 둔다.
 */
export const CATEGORY_ICONS: Partial<Record<CategoryKey, string>> = {
  pants_fits: '👖',
  pants_lengths: '👖',
  skirt_lengths: '👗',
  skirt_types: '👗',
  necklines: '👕',
  sleeves: '👕',
  top_fits: '👕',
  top_lengths: '👕',
};

export const CATEGORY_OPTIONS: Record<CategoryKey, PreferenceOption[]> = {
  seasons: [
    { code: 'spring', label: '봄' },
    { code: 'summer', label: '여름' },
    { code: 'autumn', label: '가을' },
    { code: 'winter', label: '겨울' },
  ],
  styles: [
    { code: 'minimal', label: '미니멀' },
    { code: 'casual', label: '캐주얼' },
    { code: 'street', label: '스트릿' },
    { code: 'classic', label: '클래식' },
    { code: 'lovely', label: '러블리' },
    { code: 'chic', label: '시크' },
    { code: 'sporty', label: '스포티' },
    { code: 'vintage', label: '빈티지' },
    { code: 'romantic', label: '로맨틱' },
    { code: 'elegance', label: '엘레강스' },
    { code: 'retro', label: '레트로' },
    { code: 'business', label: '비즈니스' },
    { code: 'business_casual', label: '비즈니스 캐주얼' },
    { code: 'americasual', label: '아메카지' },
    { code: 'modern', label: '모던' },
    { code: 'boyish', label: '보이시' },
  ],
  colors: [
    { code: 'black', label: '블랙', colorHex: '#000000' },
    { code: 'ivory', label: '아이보리', colorHex: '#FFFFF0' },
    { code: 'white', label: '화이트', colorHex: '#FFFFFF' },
    { code: 'gray', label: '그레이', colorHex: '#808080' },
    { code: 'charcoal', label: '차콜', colorHex: '#36454F' },
    { code: 'navy', label: '네이비', colorHex: '#000080' },
    { code: 'beige', label: '베이지', colorHex: '#F5F5DC' },
    { code: 'brown', label: '브라운', colorHex: '#8B4513' },
    { code: 'khaki', label: '카키', colorHex: '#F0E68C' },
    { code: 'olive', label: '올리브', colorHex: '#6B7A3A' },
    { code: 'carmel', label: '카멜', colorHex: '#C19A6B' },
    { code: 'denim_blue', label: '데님블루', colorHex: '#1560BD' },
    { code: 'light_pink', label: '라이트 핑크', colorHex: '#FFB6C1' },
    { code: 'pink', label: '핑크', colorHex: '#FFC0CB' },
    { code: 'rose', label: '로즈', colorHex: '#FF007F' },
    { code: 'mauve', label: '모브', colorHex: '#E0B0FF' },
    { code: 'peach', label: '피치', colorHex: '#FFDAB9' },
    { code: 'coral', label: '코럴', colorHex: '#FF7F50' },
    { code: 'light_blue', label: '라이트 블루', colorHex: '#ADD8E6' },
    { code: 'blue', label: '블루', colorHex: '#0000FF' },
    { code: 'mint', label: '민트', colorHex: '#98FF98' },
    { code: 'green', label: '그린', colorHex: '#008000' },
    { code: 'red', label: '레드', colorHex: '#FF0000' },
    { code: 'burgundy', label: '버건디', colorHex: '#800020' },
    { code: 'yellow', label: '옐로우', colorHex: '#FFFF00' },
    { code: 'purple', label: '퍼플', colorHex: '#800080' },
    { code: 'orange', label: '오렌지', colorHex: '#FFA500' },
    { code: 'silver', label: '실버', colorHex: '#C0C0C0' },
    { code: 'gold', label: '골드', colorHex: '#FFD700' },
  ],
  pants_fits: [
    { code: 'wide', label: '와이드', icon: WidePantsIcon },
    { code: 'jogger', label: '조거', icon: JoggerPantsIcon },
    { code: 'straight', label: '스트레이트', icon: StraightPantsIcon },
    { code: 'skinny', label: '스키니', icon: SkinnyPantsIcon },
    { code: 'bootcut', label: '부츠컷', icon: BootcutPantsIcon },
    { code: 'slacks', label: '슬랙스', icon: SlacksPantsIcon },
    { code: 'semi_wide', label: '세미와이드', icon: SemiWidePantsIcon },
  ],
  pants_lengths: [
    { code: 'short_shorts', label: '짧은 반바지(3부)', icon: ShortShortsIcon },
    { code: 'shorts', label: '반바지(5부)', icon: ShortsIcon },
    { code: 'seven_part', label: '7부', icon: SevenPartPantsIcon },
    { code: 'long_pants', label: '긴바지', icon: LongPantsIcon },
  ],
  skirt_lengths: [
    { code: 'mini', label: '미니', icon: MiniSkirtIcon },
    { code: 'midi', label: '미디', icon: MidiSkirtIcon },
    { code: 'long', label: '롱', icon: LongSkirtIcon },
    { code: 'maxi', label: '맥시', icon: MaxiSkirtIcon },
  ],
  skirt_types: [
    { code: 'aline', label: 'A라인', icon: ALineSkirtIcon },
    { code: 'pleats', label: '플리츠', icon: PleatsSkirtIcon },
    { code: 'flare', label: '플레어 라인', icon: FlareSkirtIcon },
    { code: 'hline', label: 'H라인', icon: HLineSkirtIcon },
    { code: 'mermaid', label: '머메이드', icon: MermaidSkirtIcon },
    { code: 'balloon', label: '벌룬', icon: BalloonSkirtIcon },
  ],
  necklines: [
    { code: 'round', label: '라운드넥', icon: RoundNeckIcon },
    { code: 'vneck', label: '브이넥', icon: VNeckIcon },
    { code: 'uneck', label: '유넥', icon: UNeckIcon },
    { code: 'hood', label: '후드', icon: HoodIcon },
    { code: 'square', label: '스퀘어넥', icon: SquareNeckIcon },
    { code: 'off_shoulder', label: '오프숄더', icon: OffShoulderIcon },
    { code: 'half_high', label: '반하이넥', icon: HalfHighNeckIcon },
    { code: 'one_shoulder', label: '원숄더', icon: OneShoulderIcon },
    { code: 'halter', label: '홀터넥', icon: HalterNeckIcon },
    { code: 'boat', label: '보트넥', icon: BoatNeckIcon },
    { code: 'heart', label: '하트넥', icon: SweetheartNeckIcon },
    { code: 'turtle', label: '터틀넥', icon: TurtleneckIcon },
    { code: 'high', label: '하이넥', icon: HighNeckIcon },
    { code: 'half_zip', label: '반집업', icon: HalfZipIcon },
  ],
  sleeves: [
    { code: 'long', label: '긴소매', icon: LongSleeveIcon },
    { code: 'short', label: '반소매', icon: ShortSleeveIcon },
    { code: 'sleeveless', label: '민소매', icon: SleevelessIcon },
    { code: 'three_quarter', label: '7부소매', icon: ThreeQuarterSleeveIcon },
  ],
  top_fits: [
    { code: 'normal', label: '노멀핏', icon: NormalFitIcon },
    { code: 'slim', label: '슬림핏', icon: SlimFitIcon },
    { code: 'loose', label: '루즈핏', icon: LooseFitIcon },
    { code: 'oversized', label: '오버핏', icon: OverFitIcon },
  ],
  top_lengths: [
    { code: 'crop', label: '크롭', icon: CropLengthIcon },
    { code: 'short', label: '숏', icon: ShortLengthIcon },
    { code: 'regular', label: '레귤러', icon: RegularLengthIcon },
    { code: 'long', label: '롱', icon: LongLengthIcon },
  ],
};

export const CATEGORY_ORDER: CategoryKey[] = [
  'seasons',
  'styles',
  'colors',
  'necklines',
  'sleeves',
  'top_fits',
  'top_lengths',
  'pants_fits',
  'pants_lengths',
  'skirt_lengths',
  'skirt_types',
];
