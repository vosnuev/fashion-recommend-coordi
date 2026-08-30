import { Pressable, StyleSheet, Text, View, type ViewStyle } from 'react-native';

import { Icon, type IconName } from '@/components/icon';
import { Editorial, ink, Type } from '@/constants/theme';

/**
 * 빈 상태 — 옷장/룩북/채팅/추천결과/검색결과가 비었을 때 공통으로 쓰는 화면.
 * 아이콘 + 제목 + 설명 + (선택) 액션 버튼. 톤은 에디토리얼 '본' 팔레트.
 *
 * 예) <EmptyState icon="magnifyingglass" title="검색 결과가 없어요"
 *        description="다른 키워드로 찾아볼까요?" />
 */
export function EmptyState({
  icon = 'tshirt',
  title,
  description,
  actionLabel,
  onAction,
  style,
}: {
  icon?: IconName;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  style?: ViewStyle;
}) {
  return (
    <View style={[styles.wrap, style]}>
      <View style={styles.iconCircle}>
        <Icon name={icon} tintColor={ink(0.32)} size={26} />
      </View>
      <Text style={styles.title}>{title}</Text>
      {description ? <Text style={styles.desc}>{description}</Text> : null}
      {actionLabel && onAction ? (
        <Pressable style={styles.action} onPress={onAction} hitSlop={8}>
          <Text style={styles.actionText}>{actionLabel}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32, paddingVertical: 48 },
  iconCircle: {
    width: 68,
    height: 68,
    borderRadius: 34,
    backgroundColor: Editorial.surfaceSoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 18,
  },
  title: { fontSize: Type.label, fontWeight: '600', color: Editorial.ink, textAlign: 'center' },
  desc: {
    fontSize: Type.footnote,
    color: Editorial.textCaption,
    textAlign: 'center',
    marginTop: 7,
    lineHeight: 20,
  },
  action: {
    marginTop: 20,
    height: 44,
    paddingHorizontal: 22,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionText: { fontSize: Type.footnote, fontWeight: '600', color: '#fff' },
});
