import { router, useLocalSearchParams } from 'expo-router';
import { goBack } from '@/lib/goBack';
import { useEffect, useMemo, useState } from 'react';
import {
  LayoutAnimation,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  UIManager,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Icon } from '@/components/icon';
import { useToast } from '@/components/ui';
import {
  CATEGORY_OPTIONS,
  CATEGORY_ORDER,
  CATEGORY_TITLES,
  type CategoryKey,
  type PreferenceMode,
  type PreferenceOption,
} from '@/constants/pursuit-options';
import { PursuitEndpoint } from '@/constants/config';
import { ContentMax, Editorial, Fonts, ink } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { ApiError, api } from '@/lib/apiClient';

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

const INK = Editorial.ink;
const WINE = Editorial.wine;

/* 선택색. 선호=파랑, 기피=빨강 계열.
   FILL = 옅게 채우는 배경색(사용자 지정), LINE = 글자용 진한 강조색. */
const PREFER_FILL = '#E8EDFF';
const AVOID_FILL = '#FFEBEB';
const PREFER_LINE = '#5C86C0';
const AVOID_LINE = '#C46A64';

/** 카테고리별 선택 코드 집합 */
type Selection = Record<CategoryKey, Set<string>>;

const emptySelection = (): Selection =>
  CATEGORY_ORDER.reduce((acc, key) => {
    acc[key] = new Set<string>();
    return acc;
  }, {} as Selection);

/** 서버 payload 형식 — 카테고리 키별 선택 코드 배열 (preferred/avoided 각각). */
type PursuitPayload = {
  preferred: Partial<Record<string, string[]>>;
  avoided: Partial<Record<string, string[]>>;
};

/** Selection(Set) → 서버 payload. 모든 카테고리 키를 담고, 빈 건 [] 로 보낸다. */
const toPayload = (sel: Selection): Record<string, string[]> =>
  CATEGORY_ORDER.reduce((acc, key) => {
    acc[key] = Array.from(sel[key]);
    return acc;
  }, {} as Record<string, string[]>);

/** 서버 payload → Selection. 없는/모르는 카테고리 키는 빈 집합으로 둔다. */
const fromPayload = (payload: Partial<Record<string, string[]>> | undefined): Selection => {
  const sel = emptySelection();
  if (!payload) return sel;
  CATEGORY_ORDER.forEach((key) => {
    const arr = payload[key];
    if (Array.isArray(arr)) sel[key] = new Set(arr);
  });
  return sel;
};

/* ── 칩 ────────────────────────────────────────────────── */

function Chip({
  option,
  state,
  onPress,
}: {
  option: PreferenceOption;
  /** 이 항목이 선호/기피 중 어느 집합에 있는지 (지금 모드와 무관하게 항상 표시) */
  state: PreferenceMode | null;
  onPress: () => void;
}) {
  const IconCmp = option.icon;

  // 선택된 칩: 옅은 색으로 채우고 글자는 진한 강조색.
  let chipSel: { backgroundColor: string; borderColor: string } | null = null;
  let textColor: string | null = null;
  if (state) {
    textColor = state === 'preferred' ? PREFER_LINE : AVOID_LINE;
    const fill = state === 'preferred' ? PREFER_FILL : AVOID_FILL;
    chipSel = { backgroundColor: fill, borderColor: fill };
  }

  return (
    <Pressable onPress={onPress} style={[styles.chip, chipSel]}>
      {option.colorHex ? (
        <View style={[styles.swatch, { backgroundColor: option.colorHex }]} />
      ) : IconCmp ? (
        <View style={styles.chipIcon}>
          <IconCmp width={18} height={18} />
        </View>
      ) : null}
      <Text style={[styles.chipText, textColor ? { color: textColor } : null]}>{option.label}</Text>
    </Pressable>
  );
}

/* ── 카테고리 ───────────────────────────────────────────── */

function CategorySection({
  title,
  collapsible,
  expanded,
  onToggle,
  children,
}: {
  title: string;
  /** 선택지가 한 줄에 다 들어가는 짧은 카테고리는 접지 않고 항상 펼쳐 둔다(아코디언·화살표 없음). */
  collapsible: boolean;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const open = collapsible ? expanded : true;
  return (
    <View style={styles.group}>
      <Pressable
        style={styles.groupHead}
        onPress={collapsible ? onToggle : undefined}
        disabled={!collapsible}>
        <Text style={styles.groupTitle}>{title}</Text>
        {collapsible ? (
          <Icon
            name="chevron.down"
            tintColor={ink(0.4)}
            size={16}
            style={expanded ? styles.chevronOpen : undefined}
          />
        ) : null}
      </Pressable>
      {open ? <View style={styles.groupBody}>{children}</View> : null}
    </View>
  );
}

/**
 * A7 추구미·선호도.
 * 선호/기피 두 모드로 나눠 카테고리별 선택지를 고른다 — 카테고리·코드 체계는 팀 설계를 따른다.
 */
export default function StyleOnboarding() {
  const { contentStyle } = useBreakpoint();
  const toast = useToast();
  const { returnTo } = useLocalSearchParams<{ returnTo?: string }>();

  const [mode, setMode] = useState<PreferenceMode>('preferred');
  const [preferred, setPreferred] = useState<Selection>(emptySelection);
  const [avoided, setAvoided] = useState<Selection>(emptySelection);
  /* 접을 수는 있되 처음에는 전부 펼쳐 둔다 — 어떤 항목이 있는지 한 번에 보이는 편이 고르기 쉽다. */
  const [openKeys, setOpenKeys] = useState<Set<CategoryKey>>(() => new Set(CATEGORY_ORDER));
  const [saving, setSaving] = useState(false);
  /** 저장된 선택을 못 불러왔다 — 이대로 저장하면 기존 것을 덮어쓴다 */
  const [loadFailed, setLoadFailed] = useState(false);

  // 진입 시 저장된 선호도를 불러와 프리필 (미로그인/미배포/최초진입이면 빈 선택으로 시작).
  useEffect(() => {
    let alive = true;
    api
      .get<PursuitPayload>(PursuitEndpoint)
      .then((data) => {
        if (!alive || !data) return;
        setPreferred(fromPayload(data.preferred));
        setAvoided(fromPayload(data.avoided));
      })
      .catch((e) => {
        /* 404(아직 저장 전)는 정상이다 — 빈 선택으로 시작하면 된다.
           그 밖의 실패는 알려야 한다. 불러오지 못한 걸 모른 채 저장하면
           **서버에 있던 기존 선택이 지금 화면의 빈 값으로 덮인다.** */
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) return;
        setLoadFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const current = mode === 'preferred' ? preferred : avoided;
  const setCurrent = mode === 'preferred' ? setPreferred : setAvoided;
  const other = mode === 'preferred' ? avoided : preferred;
  const setOther = mode === 'preferred' ? setAvoided : setPreferred;

  const toggleSection = (key: CategoryKey) => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setOpenKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleOption = (key: CategoryKey, code: string, label: string) => {
    const adding = !current[key].has(code);

    setCurrent((prev) => {
      const next = { ...prev, [key]: new Set(prev[key]) };
      if (next[key].has(code)) next[key].delete(code);
      else next[key].add(code);
      return next;
    });

    /* 같은 항목을 선호와 기피에 동시에 둘 수는 없다 — 추천이 가산점을 줘야 할지
       감점을 줘야 할지 판단할 수 없는 모순된 데이터가 된다.
       반대쪽에서 자동으로 빼되, 조용히 사라지면 혼란스러우므로 이유를 알린다. */
    if (adding && other[key].has(code)) {
      setOther((prev) => {
        const next = { ...prev, [key]: new Set(prev[key]) };
        next[key].delete(code);
        return next;
      });
      toast(
        mode === 'preferred'
          ? `'${label}' 을(를) 기피 목록에서 뺐어요`
          : `'${label}' 을(를) 선호 목록에서 뺐어요`,
      );
    }
  };

  const totalFor = (sel: Selection) =>
    CATEGORY_ORDER.reduce((n, key) => n + sel[key].size, 0);

  const preferredCount = useMemo(() => totalFor(preferred), [preferred]);
  const avoidedCount = useMemo(() => totalFor(avoided), [avoided]);
  const hasSelection = preferredCount + avoidedCount > 0;

  const goHome = () => {
    if (returnTo === 'my') router.navigate('/my');
    else router.navigate('/home');
  };

  /* "저장하고 시작하기" — 선호/기피 선택(11개 카테고리)을 통째로 저장(PUT)하고 이동한다.
     카테고리 키가 백엔드 정의와 일치한다. 저장은 best-effort: 실패해도(오프라인 등)
     이동은 하고 토스트로 알린다. */
  const saveAndFinish = async () => {
    setSaving(true);
    try {
      await api.put(PursuitEndpoint, {
        preferred: toPayload(preferred),
        avoided: toPayload(avoided),
      });
      goHome();
    } catch (e) {
      toast(
        e instanceof ApiError ? e.message : '선호도 저장에 실패했어요. 다시 시도해 주세요.',
        { variant: 'error' },
      );
      if (returnTo !== 'onboarding') goHome();
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        {/* 마이 하위 화면들과 동일한 헤더 (뒤로가기 + 가운데 제목) */}
        <View style={[styles.header, contentStyle(ContentMax.narrow)]}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/my')}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <Text style={styles.headerTitle}>추구미·선호도</Text>
          <View style={{ width: 20 }} />
        </View>

        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]}>
          <Text style={styles.title}>어떤 옷을 좋아하세요?</Text>
          <Text style={styles.lead}>
            고를수록 추천이 정확해져요. 피하고 싶은 것도 함께 알려주면 더 좋아요.
          </Text>

          {/* 선호 / 기피 모드 — 칩과 같은 채움 방식(테두리 없음) */}
          <View style={styles.modeRow}>
            <Pressable
              style={[styles.modeBtn, styles.modeBtnPrefer, mode !== 'preferred' && styles.modeBtnDim]}
              onPress={() => setMode('preferred')}>
              <Text style={styles.modeText}>선호해요</Text>
            </Pressable>
            <Pressable
              style={[styles.modeBtn, styles.modeBtnAvoid, mode !== 'avoided' && styles.modeBtnDim]}
              onPress={() => setMode('avoided')}>
              <Text style={styles.modeText}>피하고 싶어요</Text>
            </Pressable>
          </View>

          {CATEGORY_ORDER.map((key) => (
            <CategorySection
              key={key}
              title={CATEGORY_TITLES[key]}
              collapsible={CATEGORY_OPTIONS[key].length > 6}
              expanded={openKeys.has(key)}
              onToggle={() => toggleSection(key)}>
              <View style={styles.wrap}>
                {CATEGORY_OPTIONS[key].map((opt) => (
                  <Chip
                    key={opt.code}
                    option={opt}
                    state={
                      preferred[key].has(opt.code)
                        ? 'preferred'
                        : avoided[key].has(opt.code)
                          ? 'avoided'
                          : null
                    }
                    onPress={() => toggleOption(key, opt.code, opt.label)}
                  />
                ))}
              </View>
            </CategorySection>
          ))}
        </ScrollView>

        <View style={styles.bottomDivider} />
        {/* 덮어쓰기 경고 — 저장 버튼 바로 위에 둔다. 누르기 직전에 읽혀야 소용이 있다. */}
        {loadFailed ? (
          <View style={[styles.loadWarn, contentStyle(ContentMax.narrow)]}>
            <Icon name="exclamationmark.triangle" tintColor={WINE} size={14} />
            <Text style={styles.loadWarnText}>
              저장해 둔 선택을 불러오지 못했어요. 지금 저장하면 기존 선택을 덮어쓸 수 있어요.
            </Text>
          </View>
        ) : null}
        <View style={[styles.bottomBar, { paddingBottom: 12 }, contentStyle(ContentMax.narrow)]}>
          <Pressable style={styles.skipBtn} onPress={goHome} disabled={saving}>
            <Text style={styles.skipText}>나중에</Text>
          </Pressable>
          <Pressable
            style={styles.cta}
            onPress={hasSelection ? saveAndFinish : goHome}
            disabled={saving}>
            <Text style={styles.ctaText}>
              {saving ? '저장 중…' : hasSelection ? '저장하고 시작하기' : '시작하기'}
            </Text>
          </Pressable>
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.surface },
  safe: { flex: 1 },
  content: { paddingHorizontal: 24, paddingTop: 10, paddingBottom: 28 },

  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 24,
    paddingTop: 8,
    paddingBottom: 10,
  },
  headerTitle: { fontSize: 15, fontWeight: '600', color: INK },
  title: { fontFamily: Fonts.serif, fontSize: 26, color: INK },
  lead: { fontSize: 13, color: Editorial.textCaption, lineHeight: 20, marginTop: 8 },

  // 선호/기피 전환 — 각자 색으로 항상 채우고 글자는 검정. 비활성은 흐리게 해 활성을 구분.
  modeRow: { flexDirection: 'row', gap: 8, marginTop: 22 },
  modeBtn: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    height: 44,
    borderRadius: 12,
  },
  modeBtnPrefer: { backgroundColor: PREFER_FILL },
  modeBtnAvoid: { backgroundColor: AVOID_FILL },
  modeBtnDim: { opacity: 0.5 },
  modeText: { fontSize: 14, fontWeight: '600', color: INK },

  // 카테고리
  group: {
    marginTop: 12,
    borderWidth: 1,
    borderColor: ink(0.1),
    borderRadius: 16,
    overflow: 'hidden',
  },
  groupHead: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 15,
  },
  groupTitle: { flex: 1, fontSize: 14, fontWeight: '600', color: INK },
  chevronOpen: { transform: [{ rotate: '180deg' }] },
  groupBody: { paddingHorizontal: 16, paddingBottom: 16 },

  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    height: 36,
    paddingHorizontal: 14,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  chipText: { fontSize: 13, color: Editorial.textSoft, fontWeight: '500' },
  // 색상 칩의 색 견본 — 흰색 계열도 보이도록 얇은 테두리를 둔다.
  swatch: {
    width: 14,
    height: 14,
    borderRadius: 7,
    borderWidth: 1,
    borderColor: ink(0.18),
  },
  // 형태 아이콘 타일 — 흰 배경으로 선택/미선택 양쪽에서 아이콘이 보이게 한다.
  chipIcon: {
    width: 22,
    height: 22,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.surface,
  },

  loadWarn: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    paddingHorizontal: 20,
    paddingTop: 12,
  },
  loadWarnText: { flex: 1, fontSize: 12, color: WINE, lineHeight: 18 },
  bottomDivider: { height: 1, backgroundColor: ink(0.08) },
  bottomBar: {
    flexDirection: 'row',
    gap: 10,
    backgroundColor: Editorial.surface,
    paddingHorizontal: 24,
    paddingTop: 12,
  },
  skipBtn: {
    height: 52,
    paddingHorizontal: 22,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
    alignItems: 'center',
    justifyContent: 'center',
  },
  skipText: { fontSize: 14, color: Editorial.textCaption, fontWeight: '500' },
  cta: {
    flex: 1,
    height: 52,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaText: { fontSize: 15, fontWeight: '600', color: Editorial.white },
});
