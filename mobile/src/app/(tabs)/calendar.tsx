import { Icon } from '@/components/icon';
import { router } from 'expo-router';

import { withReturn } from '@/lib/goBack';
import { useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ItemMosaic } from '@/components/calendar/item-mosaic';
import { MonthPicker, type Anchor } from '@/components/calendar/month-picker';
import { ShareLookSheet } from '@/components/calendar/share-look-sheet';
import { LoginGate, SmartImage } from '@/components/ui';
import { ContentMax, Editorial, Fonts, ink, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import {
  useCalendarMonth,
  useFrequentItems,
  type FrequentItem,
} from '@/hooks/use-calendar';
import { useAuth } from '@/state/auth';
import { calendarStore, parseDateKey, toDateKey, todayKey } from '@/state/calendar';
import { useSavedLooks } from '@/state/saved';

const INK = Editorial.ink;

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

/** 날짜 칸의 가로:세로 — 룩 사진이 세로형이라 살짝 길게. 높이를 계산할 때도 이 비율을 상한으로 쓴다. */
const CELL_RATIO = 0.82;

const TODAY = todayKey();

// B2 착장 캘린더 — 월 그리드 + 선택일 상세(기록·공유)
export default function Calendar() {
  const { isLoggedIn } = useAuth();
  const { contentStyle, isDesktop, height } = useBreakpoint();
  const savedLooks = useSavedLooks();

  const now = useMemo(() => new Date(), []);
  const [view, setView] = useState({ year: now.getFullYear(), month: now.getMonth() + 1 });
  /* 보고 있는 달만 서버에서 받는다 — 달을 넘기면 그 달을 다시 불러온다. */
  const { entries, loading, error, reload } = useCalendarMonth(view.year, view.month, isLoggedIn);
  const [selectedDay, setSelectedDay] = useState(now.getDate());
  const [shareOpen, setShareOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  /* 드롭다운을 달 이름 바로 아래에 붙이려면 눌린 자리의 화면 좌표가 필요하다 */
  const monthBtnRef = useRef<View>(null);
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  const openPicker = () => {
    monthBtnRef.current?.measureInWindow((x, y, width, height) => {
      setAnchor({ x, y, width, height });
      setPickerOpen(true);
    });
  };

  const { cells, selectedKey } = useMemo(() => {
    const first = new Date(view.year, view.month - 1, 1).getDay();
    const days = new Date(view.year, view.month, 0).getDate();
    return {
      cells: [
        ...Array<number | null>(first).fill(null),
        ...Array.from({ length: days }, (_, i) => i + 1),
      ],
      selectedKey: toDateKey(view.year, view.month, selectedDay),
    };
  }, [view, selectedDay]);

  const entry = entries[selectedKey];
  /* 오늘은 아직 입는 중이라 '지난 날'로 보지 않는다 — 채워 넣기보다 고르기가 먼저다. */
  const isPast = selectedKey < TODAY;
  /* 자주 입은 옷 지름길은 넓은 화면에서만 — 좁은 화면에선 카드가 길어져 정작 눌러야 할
     '기록하기'가 접히는 곳 아래로 밀린다. 안 보여줄 화면에선 조회도 하지 않는다. */
  const showFrequent = isDesktop;
  const { items: frequentItems } = useFrequentItems(isLoggedIn && showFrequent);
  /* 이 기록과 같이 만들어진 룩북 룩. 룩북에서 지웠으면 못 찾으니 그때는 연결을 감춘다. */
  const linkedLook = savedLooks.find((l) => l.id === entry?.lookId);

  /* 데스크톱에선 달력이 스크롤 없이 한 화면에 들어와야 한다.
     화면 높이에서 머리(월 네비·요일 줄)와 아래 여백으로 쓰이는 몫을 뺀 나머지를
     그 달의 주 수로 나눠 셀 높이를 정한다. 모바일은 기존처럼 가로:세로 비율로 둔다
     (한 손 스크롤이 자연스럽고, 셀을 줄이면 사진이 너무 작아진다).

     ⚠️ 높이만 화면에 맞추면 창이 높고 달력 열이 좁을 때 날짜 칸이 세로로 길쭉해진다.
     그래서 실제 그리드 폭을 재서 **모바일과 같은 비율(0.82)을 넘지 않도록** 함께 묶는다. */
  const weeks = Math.ceil(cells.length / 7);
  /* 월 네비 56 + 요일 줄 30 + 아래 여백 32 ≈ 118. 여유를 조금 얹은 값. */
  const CHROME = 160;
  const [gridWidth, setGridWidth] = useState(0);
  const cellHeight =
    isDesktop && gridWidth > 0
      ? Math.max(
          72,
          Math.floor(Math.min((height - CHROME) / weeks, gridWidth / 7 / CELL_RATIO)),
        )
      : undefined;

  const moveMonth = (delta: number) => {
    const d = new Date(view.year, view.month - 1 + delta, 1);
    setView({ year: d.getFullYear(), month: d.getMonth() + 1 });
    setSelectedDay(1);
  };

  const openEntry = (dateKey: string) => router.push(`/calendar-entry?date=${dateKey}`);

  /* 자주 입은 옷을 눌러 들어가면 그 옷이 담긴 채로 기록 화면이 열린다 —
     인사이트를 읽을거리가 아니라 입력을 줄이는 지름길로 쓰는 게 목적이다. */
  const fillWith = (item: FrequentItem) => {
    calendarStore.seedItems([
      { id: item.id, source: 'closet', name: item.name, image: item.image },
    ]);
    openEntry(selectedKey);
  };

  // 착장 기록은 내 데이터라 비회원에게 보여줄 것이 없다. (훅 순서 유지를 위해 전부 호출한 뒤 분기)
  if (!isLoggedIn) {
    return (
      <LoginGate
        title="착장 기록은 로그인하고 볼 수 있어요"
        body="입은 옷을 날짜별로 남겨두면 다시 꺼내 보기 쉬워요."
      />
    );
  }

  return (
    <View style={styles.container}>
      {/* 헤더를 두지 않는다 — 화면 이름은 탭에 이미 있고, 뒤로 갈 곳도 탭이 맡는다.
          기록은 오른쪽(좁은 화면에선 아래) 선택일 상세의 '이 날 착장 기록하기'로 한다.
          SafeAreaView 는 남긴다: 위쪽 노치만큼은 여전히 비켜야 한다. */}
      <SafeAreaView edges={['top']} style={styles.headerSafe} />

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[styles.content, contentStyle(ContentMax.wide)]}>
        {/* 데스크톱은 달력 옆에 선택일 상세를 둔다 — 아래로 쌓으면 한 화면에 들어오지 않는다. */}
        <View style={[isDesktop && styles.twoPane]}>
        <View style={[isDesktop && styles.calendarCol]}>
        {/* 월 네비 */}
        <View style={styles.monthRow}>
          <Pressable hitSlop={10} onPress={() => moveMonth(-1)}>
            <Icon name="chevron.left" tintColor={ink(0.4)} size={16} />
          </Pressable>
          {/* 년·월을 직접 고르는 자리 — 화살표만으로는 작년까지 가는 데 열두 번을 눌러야 한다.
              좌우 화살표가 이미 양옆에 있어 여기에 표시를 더 두면 화살표만 늘어난다. */}
          <Pressable ref={monthBtnRef} onPress={openPicker} hitSlop={10}>
            <Text style={styles.monthText}>
              {view.year}년 {view.month}월
            </Text>
          </Pressable>
          <Pressable hitSlop={10} onPress={() => moveMonth(1)}>
            <Icon name="chevron.right" tintColor={ink(0.4)} size={16} />
          </Pressable>
        </View>

        {/* 불러오기 상태 — 그리드를 가리지 않고 한 줄만 쓴다.
            달을 넘길 때마다 달력이 통째로 사라지면 위치 감각을 잃는다. */}
        {error ? (
          <Pressable style={styles.loadNote} onPress={reload}>
            <Text style={styles.loadNoteText} numberOfLines={1}>
              {error}
            </Text>
            <Text style={styles.loadNoteAction}>다시 시도</Text>
          </Pressable>
        ) : loading ? (
          <View style={styles.loadNote}>
            <ActivityIndicator size="small" color={Editorial.textCaption} />
            <Text style={styles.loadNoteText}>기록을 불러오는 중…</Text>
          </View>
        ) : null}

        {/* 요일 헤더 */}
        <View style={styles.weekHeader}>
          {WEEKDAYS.map((d, i) => (
            <Text
              key={d}
              style={[
                styles.weekday,
                i === 0 && { color: '#c0392b' },
                i === 6 && { color: Editorial.textCaption },
              ]}>
              {d}
            </Text>
          ))}
        </View>

        {/* 날짜 그리드 — 기록이 있는 날은 룩 사진이 셀 배경이 된다 */}
        <View
          style={styles.grid}
          onLayout={(e) => setGridWidth(e.nativeEvent.layout.width)}>
          {cells.map((day, idx) => {
            // 높이를 정해 주면 aspectRatio 는 무시된다 → 데스크톱만 계산값을 얹는다.
            const cellStyle = [styles.cell, cellHeight ? { height: cellHeight } : styles.cellRatio];
            if (day === null) return <View key={`e${idx}`} style={cellStyle} />;
            const key = toDateKey(view.year, view.month, day);
            const rec = entries[key];
            const on = day === selectedDay;
            return (
              <Pressable key={day} style={cellStyle} onPress={() => setSelectedDay(day)}>
                <View style={[styles.dayInner, on && styles.dayInnerOn]}>
                  {rec?.photo ? (
                    <>
                      <SmartImage uri={rec.photo} width="100%" radius={11} style={styles.dayThumb} />
                      {/* 사진 위에서도 날짜가 읽히도록 얇게 덮는다 */}
                      <View style={styles.dayScrim} />
                    </>
                  ) : rec ? (
                    <View style={styles.dayFill} />
                  ) : null}
                  <Text
                    style={[
                      styles.dayNum,
                      rec && styles.dayNumRec,
                      on && styles.dayNumOn,
                      rec?.photo && styles.dayNumOnPhoto,
                      key === TODAY && styles.dayNumToday,
                    ]}>
                    {day}
                  </Text>
                  {rec?.shared ? (
                    <View style={[styles.sharedDot, !rec.photo && styles.sharedDotDark]} />
                  ) : null}
                </View>
              </Pressable>
            );
          })}
        </View>
        </View>

        {/* 선택일 상세 */}
        <View style={[styles.detail, isDesktop && styles.detailCol]}>
          <View style={styles.detailHead}>
            <Text style={styles.detailDate}>
              {view.month}월 {selectedDay}일
            </Text>
            {entry ? (
              <Pressable
                style={styles.shareBtn}
                onPress={() => setShareOpen(true)}
                hitSlop={8}>
                <Icon name="square.and.arrow.up" tintColor={INK} size={15} />
                <Text style={styles.shareBtnText}>공유</Text>
              </Pressable>
            ) : null}
          </View>

          {entry ? (
            <>
              <Pressable style={styles.recCard} onPress={() => openEntry(selectedKey)}>
                <SmartImage uri={entry.photo} width={84} height={100} radius={12} />
                <View style={styles.recBody}>
                  {/* 일정이 있으면 그게 제목이 된다 — '옷 2개 기록'보다 그날을 잘 가리킨다. */}
                  <Text style={styles.recTitle} numberOfLines={1}>
                    {entry.note ??
                      (entry.items.length > 0 ? `옷 ${entry.items.length}개 기록` : '룩 사진 기록')}
                  </Text>
                  {entry.note ? (
                    <Text style={styles.recSub}>
                      {entry.items.length > 0 ? `옷 ${entry.items.length}개 기록` : '룩 사진 기록'}
                    </Text>
                  ) : null}
                  <View style={styles.recTags}>
                    {entry.tags.map((t) => (
                      <Text key={t} style={styles.recTag}>
                        #{t}
                      </Text>
                    ))}
                  </View>
                </View>
                <Icon name="chevron.right" tintColor={ink(0.25)} size={15} />
              </Pressable>

              {/* 담긴 옷 미리보기 */}
              {entry.items.length > 0 ? (
                <View style={styles.mosaic}>
                  <ItemMosaic items={entry.items} onPress={() => openEntry(selectedKey)} />
                </View>
              ) : null}

              {/* 사진에서 옷을 뽑는 중 — 목록이 비어 보이는 게 오류가 아니라는 걸 알린다.
                  끝나면 스토어가 기록을 다시 받아 채우므로 사용자가 할 일은 없다. */}
              {entry.status === 'REGISTERED' || entry.status === 'PROCESSING' ? (
                <View style={styles.processingRow}>
                  <ActivityIndicator size="small" color={Editorial.textCaption} />
                  <Text style={styles.processingText}>사진에서 옷을 정리하고 있어요</Text>
                </View>
              ) : entry.status === 'FAILED' && entry.items.length === 0 ? (
                <Text style={styles.processingText}>
                  사진에서 옷을 찾지 못했어요. 옷은 직접 담을 수 있어요.
                </Text>
              ) : null}

              {/* 룩북에 같이 올린 룩 — 룩이 지워졌으면 줄을 그리지 않는다 */}
              {linkedLook ? (
                <Pressable
                  style={styles.lookLink}
                  onPress={() => router.push(withReturn(`/saved-look?id=${linkedLook.id}`, '/(tabs)/calendar'))}>
                  <Icon name="book" tintColor={INK} size={15} />
                  <Text style={styles.lookLinkText}>룩북에도 올린 룩이에요</Text>
                  <Icon name="chevron.right" tintColor={ink(0.3)} size={14} />
                </Pressable>
              ) : null}
            </>
          ) : (
            <View style={[styles.empty, isDesktop && styles.emptyTall]}>
              <View style={styles.emptyIcon}>
                <Icon name="tshirt" tintColor={ink(0.3)} size={26} />
              </View>
              <Text style={styles.emptyText}>이 날의 착장을 기록해 보세요</Text>

              {/* 이 카드가 뜬 이유는 '이 날 기록이 없어서'다. 그러니 지난 날이든 앞날이든
                  기록하기가 먼저고, 추천은 그다음이다. */}
              <View style={styles.actions}>
                <Pressable style={styles.primaryBtn} onPress={() => openEntry(selectedKey)}>
                  <Icon name="plus" tintColor="#fff" size={17} />
                  <Text style={styles.primaryText}>이 날 착장 기록하기</Text>
                </Pressable>
                <Pressable style={styles.secondaryBtn} onPress={() => router.push('/chat-mode')}>
                  <Icon name="sparkles" tintColor={INK} size={17} />
                  <Text style={styles.secondaryText}>코디 추천받기</Text>
                </Pressable>
              </View>

              {/* 지름길은 두 버튼 아래에 둔다 — 이 카드가 먼저 시키는 일(기록·추천)을
                  밀어내지 않으면서, 지난 날을 채울 때는 바로 손이 닿는 자리다. */}
              {isPast && showFrequent ? (
                <FrequentShortcut items={frequentItems} onPick={fillWith} />
              ) : null}
            </View>
          )}
        </View>
        </View>
      </ScrollView>

      <MonthPicker
        visible={pickerOpen}
        anchor={anchor}
        year={view.year}
        month={view.month}
        onClose={() => setPickerOpen(false)}
        onSelect={(year, month) => {
          setView({ year, month });
          setSelectedDay(1);
        }}
      />

      {entry ? (
        <ShareLookSheet
          entry={entry}
          visible={shareOpen}
          onClose={() => setShareOpen(false)}
          onToggleShared={(next) => calendarStore.setShared(entry.date, next)}
        />
      ) : null}
    </View>
  );
}

/** '2026-08-12' → '8/12'. 썸네일 아래 좁은 칸이라 년도와 요일은 뺀다. */
function shortDate(key: string): string {
  const { month, day } = parseDateKey(key);
  return `${month}/${day}`;
}

/**
 * 지난 빈 날에 보여주는 지름길 — 입었던 옷을 눌러 바로 기록을 시작한다.
 *
 * 기록 수로 막지 않는다. 한 벌만 입었어도 그게 다음 기록의 지름길이 된다.
 * 대신 **부르는 이름을 맞춘다** — 두 번 이상 입은 옷이 있을 때만 '자주'다.
 * 한 번뿐인 옷에 "1번"이라 적으면 초라해 보여, 그 자리엔 입은 날짜를 넣는다.
 */
function FrequentShortcut({
  items,
  onPick,
}: {
  items: FrequentItem[];
  onPick: (item: FrequentItem) => void;
}) {
  if (items.length === 0) {
    return (
      <Text style={styles.frequentHint}>
        입은 옷을 기록해두면 다음부터 여기서 바로 고를 수 있어요
      </Text>
    );
  }

  const repeated = items.some((item) => item.count > 1);
  return (
    <View style={styles.frequentBox}>
      <Text style={styles.frequentTitle}>{repeated ? '자주 입은 옷' : '최근에 입은 옷'}</Text>
      <View style={styles.frequentRow}>
        {items.map((item) => (
          <Pressable key={item.id} style={styles.frequentItem} onPress={() => onPick(item)}>
            {item.image ? (
              <SmartImage uri={item.image} width="100%" radius={10} style={styles.frequentThumb} />
            ) : (
              <View style={[styles.frequentThumb, styles.frequentThumbEmpty]}>
                <Icon name="tshirt" tintColor={ink(0.3)} size={18} />
              </View>
            )}
            <Text style={styles.frequentName} numberOfLines={1}>
              {item.name}
            </Text>
            <Text style={styles.frequentCount}>
              {item.count > 1 ? `${item.count}번` : shortDate(item.lastWorn)}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  headerSafe: { backgroundColor: Editorial.page },
  content: { paddingHorizontal: 16, paddingBottom: 32 },
  monthRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 20,
    paddingVertical: 16,
  },
  monthText: { fontFamily: Fonts.serif, fontSize: 19, color: INK },

  frequentHint: { marginTop: 4, fontSize: 12, color: Editorial.textCaption, textAlign: 'center' },
  frequentBox: { alignSelf: 'stretch', marginTop: 4, marginBottom: 4, gap: 8 },
  frequentTitle: { fontSize: 12, fontWeight: '600', color: Editorial.textCaption, textAlign: 'center' },
  /* 개수가 적어도 카드의 다른 요소(아이콘·문구·버튼)와 같이 가운데에 선다 */
  frequentRow: { flexDirection: 'row', justifyContent: 'center', gap: 8 },
  /* 상한은 3개일 때의 한 칸 크기 — 패널 400 - 카드 여백 40 - gap 16, 나누기 3.
     상한이 없으면 옷이 하나뿐인 날 썸네일이 카드를 다 먹고 버튼을 아래로 민다. */
  frequentItem: { flex: 1, maxWidth: 112, alignItems: 'center', gap: 4 },
  frequentThumb: { aspectRatio: 1, backgroundColor: Editorial.surface },
  frequentThumbEmpty: {
    width: '100%',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  frequentName: { fontSize: 11, color: INK, textAlign: 'center' },
  frequentCount: { fontSize: 10, color: Editorial.textCaption },
  processingRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 10 },
  processingText: { fontSize: 12, color: Editorial.textCaption },
  loadNote: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 4,
    paddingBottom: 8,
  },
  loadNoteText: { flex: 1, fontSize: 12, color: Editorial.textCaption },
  loadNoteAction: { fontSize: 12, fontWeight: '600', color: Editorial.selected },
  weekHeader: { flexDirection: 'row', paddingBottom: 6 },
  weekday: {
    flex: 1,
    textAlign: 'center',
    fontSize: Type.micro,
    color: Editorial.textCaption,
    fontWeight: '500',
  },

  /* 데스크톱 2단 — 왼쪽 달력이 남는 폭을 채우고, 오른쪽 상세는 고정 폭. */
  twoPane: { flexDirection: 'row', alignItems: 'flex-start', gap: 28 },
  calendarCol: { flex: 1, minWidth: 0 },
  detailCol: { width: 400, flexShrink: 0, marginTop: 16 },

  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  cell: {
    width: `${100 / 7}%`,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 4,
    paddingVertical: 3,
  },
  // 모바일 기본 — 높이를 정하지 않고 가로:세로 비율로 둔다.
  cellRatio: { aspectRatio: CELL_RATIO },
  /* 칸을 부모 크기의 백분율(86%/90%)로 잡지 않는다. 부모 높이가 aspectRatio 로 뒤늦게
     정해지는 구조라 안드로이드에서는 백분율 높이가 0으로 풀릴 때가 있는데, 그러면
     overflow:'hidden' 이 안쪽 날짜 글자를 통째로 잘라 먹는다. 부모 패딩 + flex 로 채운다. */
  dayInner: {
    flex: 1,
    alignSelf: 'stretch',
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
    /* 선택 표시는 테두리 '색'만 바꾼다. 누를 때마다 borderWidth 를 넣었다 뺐다 하면
       둥근 모서리 클리핑이 다시 계산되면서(안드로이드) 글자가 사라지고, 글자도 1.5px 씩 밀린다. */
    borderWidth: 1.5,
    borderColor: 'transparent',
  },
  dayInnerOn: { borderColor: Editorial.selected },
  dayThumb: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },
  dayScrim: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: ink(0.3) },
  /* 사진 없이 옷만 기록한 날 — 사진 대신 옅은 면으로 '기록 있음'을 표시 */
  dayFill: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    borderRadius: 11,
    backgroundColor: ink(0.07),
  },
  dayNum: { fontSize: Type.caption, color: Editorial.textCaption },
  dayNumRec: { color: INK, fontWeight: '700' },
  dayNumOn: { fontWeight: '700', color: INK },
  dayNumOnPhoto: { color: '#fff' },
  dayNumToday: { textDecorationLine: 'underline' },
  sharedDot: {
    position: 'absolute',
    bottom: 5,
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#fff',
  },
  sharedDotDark: { backgroundColor: ink(0.45) },

  detail: { marginTop: 22 },
  detailHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
    marginLeft: 4,
  },
  detailDate: { fontFamily: Fonts.serif, fontSize: 19, color: INK },
  shareBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 12,
    height: 32,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  shareBtnText: { fontSize: Type.micro, fontWeight: '600', color: INK },

  recCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 16,
    padding: 12,
  },
  recBody: { flex: 1, gap: 6 },
  recTitle: { fontSize: Type.label, fontWeight: '600', color: INK },
  recSub: { fontSize: Type.caption, color: Editorial.textCaption, marginTop: -2 },
  recTags: { flexDirection: 'row', gap: 8 },
  recTag: { fontSize: Type.caption, color: Editorial.textCaption },

  mosaic: { marginTop: 10 },

  lookLink: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 10,
    paddingHorizontal: 14,
    height: 46,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  lookLinkText: { flex: 1, fontSize: Type.caption, fontWeight: '600', color: INK },


  empty: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 16,
    borderWidth: 1,
    borderColor: Editorial.line,
    borderStyle: 'dashed',
    borderRadius: 16,
    paddingVertical: 30,
    paddingHorizontal: 20,
  },
  /* 데스크톱에선 옆 달력이 화면을 꽉 채워, 이 카드가 작으면 빈 날이 '없는 기능'처럼 보인다 */
  emptyTall: { minHeight: 360, paddingVertical: 40 },
  emptyIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyText: { fontSize: Type.body, color: Editorial.textCaption },
  /* 두 버튼은 '무엇을 먼저 할지'가 다를 뿐 둘 다 할 수 있는 일이다. 크기를 다르게 두면
     작은 쪽이 안내문처럼 보여 눌러도 되는지 헷갈린다 — 크기는 맞추고 채움/테두리로만 순서를 준다. */
  /* 카드 폭을 꽉 채우면 점선 테두리에 눌린 것처럼 보인다 — 가운데로 모으고 폭을 제한한다. */
  actions: { alignSelf: 'stretch', alignItems: 'center', gap: 8 },
  primaryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    width: '100%',
    maxWidth: 280,
    height: 46,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
  },
  primaryText: { fontSize: Type.label, color: '#fff', fontWeight: '600' },
  secondaryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 7,
    width: '100%',
    maxWidth: 280,
    height: 46,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  secondaryText: { fontSize: Type.label, color: INK, fontWeight: '500' },
});
