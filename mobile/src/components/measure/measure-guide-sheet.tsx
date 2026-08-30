import { useEffect, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Icon } from '@/components/icon';
import {
  BODY_MEASURES,
  BODY_MEASURE_BY_KEY,
  type BodyMeasureKey,
} from '@/constants/body-measures';
import { Editorial, ink, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';

import { BodyFigureAll } from './body-figure';

const INK = Editorial.ink;

/**
 * '재는 법' 안내 시트 — 마네킹 하나에 10곳을 전부 찍고, 번호로 목록과 잇는다.
 *
 * 값만 보여주면 사용자는 어깨너비를 등을 돌아 재고 4~5cm 크게 적는다(가장 흔한 오차).
 * 숫자를 고칠 수 있게 열어 둔 이상, 기준을 그림으로 같이 줘야 고친 값이 쓸모 있다.
 *
 * 항목을 하나씩 넘겨 보게 하지 않는다 — 열 곳의 위아래 관계가 안 보이고, 찾는 항목까지
 * 몇 번을 넘겨야 하는지 모른 채 넘기게 된다. 대신 **목록을 누르면** 그 번호만 진해지고
 * 재는 순서도 그 항목 것으로 바뀐다. 같은 항목을 다시 누르면 전체 보기로 돌아온다.
 */
export function MeasureGuideSheet({
  visible,
  measureKey,
  onClose,
}: {
  visible: boolean;
  /** 열 때 강조할 항목. null 이면 10개를 같은 세기로 보여준다 */
  measureKey: BodyMeasureKey | null;
  onClose: () => void;
}) {
  const { isDesktop } = useBreakpoint();
  const [selected, setSelected] = useState<BodyMeasureKey | null>(measureKey);

  // 열 때마다 호출부가 지정한 항목으로 되돌린다 (지난번에 눌러 둔 항목이 남지 않게).
  useEffect(() => {
    if (visible) setSelected(measureKey);
  }, [visible, measureKey]);

  /* 재는 순서는 고른 항목 것을 보여주고, 아무것도 안 골랐으면 어깨너비 것을 쓴다 —
     10개 순서를 한 번에 늘어놓으면 30줄이 되어 정작 가장 많이 틀리는 어깨가 묻힌다. */
  const noted = BODY_MEASURE_BY_KEY[selected ?? 'shoulder'];

  return (
    <Modal
      visible={visible}
      transparent
      animationType={isDesktop ? 'fade' : 'slide'}
      onRequestClose={onClose}>
      {/* 배경 닫기는 **뒤에 깔린** Pressable 이 받는다. 시트를 Pressable 로 감싸면
          그 responder 가 ScrollView 의 스크롤 제스처를 먼저 채 가서 목록이 안 움직인다. */}
      <View style={[styles.root, isDesktop && styles.rootCenter]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />

        <View style={[styles.sheet, isDesktop && styles.dialog]}>
          {isDesktop ? null : <View style={styles.handle} />}

          <View style={styles.head}>
            <Text style={styles.title}>재는 법</Text>
            <Pressable hitSlop={10} onPress={onClose}>
              <Icon name="xmark" tintColor={ink(0.5)} size={20} />
            </Pressable>
          </View>

          <ScrollView style={styles.scroll} contentContainerStyle={styles.body}>
            <View style={styles.figureWrap}>
              <BodyFigureAll highlight={selected} width={228} />
            </View>

            <Text style={styles.listHint}>항목을 누르면 그 위치만 진하게 보여요</Text>

            <View style={styles.list}>
              {BODY_MEASURES.map((spec, i) => {
                const on = spec.key === selected;
                return (
                  <Pressable
                    key={spec.key}
                    style={[styles.row, on && styles.rowOn]}
                    onPress={() => setSelected(on ? null : spec.key)}>
                    <View style={[styles.no, on && styles.noOn]}>
                      <Text style={[styles.noText, on && styles.noTextOn]}>{i + 1}</Text>
                    </View>
                    <View style={styles.rowTexts}>
                      <Text style={[styles.rowLabel, on && styles.rowLabelOn]}>{spec.label}</Text>
                      <Text style={styles.rowSummary}>{spec.summary}</Text>
                    </View>
                  </Pressable>
                );
              })}
            </View>

            {/* 재는 순서는 고른 항목 것만 편다 (기본은 어깨너비) */}
            <View style={styles.detail}>
              <Text style={styles.detailHead}>{noted.label} 자세히</Text>
              {/* 이 값이 추천의 무엇을 바꾸는지 — 비율 3개는 이걸 모르면 왜 재는지 알 수 없다 */}
              {noted.caption ? <Text style={styles.detailUsage}>{noted.caption}</Text> : null}
              {noted.steps.map((step, i) => (
                <View key={step} style={styles.stepRow}>
                  <Text style={styles.stepNo}>{i + 1}</Text>
                  <Text style={styles.stepText}>{step}</Text>
                </View>
              ))}
              {noted.caution ? (
                <View style={styles.caution}>
                  <Icon name="exclamationmark.triangle" tintColor={Editorial.wine} size={14} />
                  <Text style={styles.cautionText}>{noted.caution}</Text>
                </View>
              ) : null}
            </View>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: ink(0.42), justifyContent: 'flex-end' },
  rootCenter: { justifyContent: 'center', alignItems: 'center', padding: 24 },

  sheet: {
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    paddingHorizontal: 20,
    paddingBottom: 28,
    maxHeight: '88%',
  },
  dialog: {
    width: '100%',
    maxWidth: 440,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: Editorial.line,
    paddingTop: 20,
    maxHeight: '86%',
  },
  handle: {
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: ink(0.14),
    alignSelf: 'center',
    marginTop: 10,
    marginBottom: 14,
  },

  head: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  title: { fontSize: Type.lead, fontWeight: '600', color: INK },

  /* 시트 높이가 maxHeight 로 잘리는 구조라, 스크롤 영역이 남은 자리만큼 줄어들 수 있어야 한다.
     flexShrink 가 없으면 내용 높이 그대로 커져 아래가 잘리고 스크롤도 안 먹는다. */
  scroll: { flexShrink: 1 },
  body: { paddingTop: 12, paddingBottom: 8 },
  figureWrap: {
    alignItems: 'center',
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: Editorial.lineSoft,
    borderRadius: 16,
  },

  listHint: { fontSize: Type.micro, color: Editorial.textCaption, marginTop: 14 },
  list: { marginTop: 6, gap: 2 },
  row: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 8,
    borderRadius: 10,
  },
  rowOn: { backgroundColor: ink(0.05) },
  no: {
    width: 19,
    height: 19,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  noOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  noText: { fontSize: 10.5, fontWeight: '700', color: Editorial.textCaption },
  noTextOn: { color: Editorial.white },
  rowTexts: { flex: 1, gap: 1 },
  rowLabel: { fontSize: Type.footnote, color: INK, fontWeight: '500' },
  rowLabelOn: { fontWeight: '700' },
  rowSummary: { fontSize: Type.caption, color: Editorial.textCaption, lineHeight: 19 },

  detail: {
    marginTop: 18,
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: Editorial.lineSoft,
    gap: 9,
  },
  detailHead: { fontSize: Type.footnote, fontWeight: '600', color: INK, marginBottom: 1 },
  detailUsage: { fontSize: Type.caption, color: Editorial.textCaption, lineHeight: 19 },
  stepRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  stepNo: {
    width: 19,
    height: 19,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Editorial.line,
    textAlign: 'center',
    lineHeight: 18,
    fontSize: 10.5,
    color: Editorial.textCaption,
  },
  stepText: { flex: 1, fontSize: Type.caption, color: Editorial.textSoft, lineHeight: 20 },

  caution: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    marginTop: 4,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  cautionText: { flex: 1, fontSize: Type.caption, color: Editorial.textSoft, lineHeight: 19 },
});
