import { Icon } from '@/components/icon';
import { goBack } from '@/lib/goBack';
import { ScrollView, StyleSheet, Switch, Text, View, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ContentMax, Editorial, ink, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { notiStore, useNotifications, type NotiKey } from '@/state/notifications';

const INK = Editorial.ink;

const SECTIONS: { title: string; rows: { key: NotiKey; label: string; desc: string }[] }[] = [
  {
    title: '추천',
    rows: [
      { key: 'dailyLook', label: '오늘의 룩 알림', desc: '매일 아침 오늘의 추천 룩을 알려드려요.' },
      { key: 'weather', label: '날씨 변화 알림', desc: '기온·날씨가 크게 바뀌면 코디를 다시 제안해요.' },
    ],
  },
  {
    title: '내 활동',
    rows: [{ key: 'savedUpdates', label: '저장 소식', desc: '저장한 룩과 관련된 새 소식을 받아요.' }],
  },
  {
    title: '혜택',
    rows: [{ key: 'marketing', label: '이벤트·혜택', desc: '이벤트와 할인 소식을 받아요.' }],
  },
];

// 마이 › 알림 설정 — 알림 종류별 켜기/끄기 (notiStore 에 보관).
export default function NotificationsScreen() {
  const { contentStyle } = useBreakpoint();
  const noti = useNotifications();

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        <View style={[styles.header, contentStyle(ContentMax.narrow)]}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/my')}>
            <Icon name="chevron.left" tintColor={INK} size={22} />
          </Pressable>
          <Text style={styles.headerTitle}>알림 설정</Text>
          <View style={styles.headerSpacer} />
        </View>

        <ScrollView
          contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]}
          showsVerticalScrollIndicator={false}>
          {SECTIONS.map((section) => (
            <View key={section.title} style={styles.section}>
              <Text style={styles.sectionTitle}>{section.title}</Text>
              <View style={styles.card}>
                {section.rows.map((row, i) => (
                  <View key={row.key} style={[styles.row, i > 0 && styles.rowDivider]}>
                    <View style={styles.rowText}>
                      <Text style={styles.rowLabel}>{row.label}</Text>
                      <Text style={styles.rowDesc}>{row.desc}</Text>
                    </View>
                    <Switch
                      value={noti[row.key]}
                      onValueChange={(v) => notiStore.set(row.key, v)}
                      trackColor={{ false: ink(0.12), true: INK }}
                      ios_backgroundColor={ink(0.12)}
                    />
                  </View>
                ))}
              </View>
            </View>
          ))}

          <Text style={styles.foot}>
            기기 설정에서 앱 알림을 꺼두면 위 설정과 무관하게 알림이 오지 않아요.
          </Text>
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },

  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 12,
  },
  headerTitle: { fontSize: Type.label, fontWeight: '700', color: INK },
  headerSpacer: { width: 22 },

  content: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 32 },

  section: { marginTop: 22 },
  sectionTitle: { fontSize: Type.caption, fontWeight: '600', color: Editorial.textCaption, marginBottom: 10, marginLeft: 4 },
  card: {
    borderWidth: 1,
    borderColor: ink(0.1),
    borderRadius: 18,
    paddingHorizontal: 16,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    paddingVertical: 16,
  },
  rowDivider: { borderTopWidth: 1, borderTopColor: ink(0.07) },
  rowText: { flex: 1, gap: 3 },
  rowLabel: { fontSize: Type.body, fontWeight: '600', color: INK },
  rowDesc: { fontSize: Type.caption, color: Editorial.textCaption, lineHeight: 18 },

  foot: { fontSize: Type.micro, color: Editorial.textCaption, lineHeight: 18, marginTop: 22, textAlign: 'center' },
});
