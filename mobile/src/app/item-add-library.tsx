import { Editorial, ink, ContentMax } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { Icon } from '@/components/icon';
import { SmartImage, ModalShell } from '@/components/ui';
import { LIBRARY_ITEMS } from '@/constants/wardrobe';
import { draftItem } from '@/state/draft-item';
import { router } from 'expo-router';
import { goBack } from '@/lib/goBack';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

const INK = Editorial.ink;
const PAD = 20;

export default function ItemAddLibraryScreen() {
  const { contentStyle } = useBreakpoint();
  const pick = (item: (typeof LIBRARY_ITEMS)[number]) => {
    if (!item.image) return;
    draftItem.setPhoto(item.image);
    draftItem.setLibraryItem({ name: item.name, category: item.category });
    router.replace('/item-add');
  };

  return (
    <ModalShell maxWidth={ContentMax.default}>
      <View style={styles.container}>
      <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
        <View style={[styles.header, contentStyle(ContentMax.default)]}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/closet')}>
            <Icon name="chevron.left" tintColor={INK} size={22} />
          </Pressable>
          <Text style={styles.title}>라이브러리</Text>
          <View style={styles.headerSpacer} />
        </View>
        <Text style={styles.hint}>카탈로그에서 옷을 골라 옷장에 추가해요</Text>

        <ScrollView contentContainerStyle={[styles.list, contentStyle(ContentMax.default)]}>
          {LIBRARY_ITEMS.map((item) => (
            <Pressable key={item.id} style={styles.row} onPress={() => pick(item)}>
              <SmartImage uri={item.image} width={64} height={80} radius={10} />
              <View style={styles.rowText}>
                <Text style={styles.rowName}>{item.name}</Text>
                <Text style={styles.rowBrand}>{item.brand}</Text>
              </View>
              <Icon name="chevron.right" tintColor={ink(0.3)} size={18} />
            </Pressable>
          ))}
        </ScrollView>
      </SafeAreaView>
      </View>
    </ModalShell>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: PAD,
    paddingVertical: 12,
  },
  title: { flex: 1, textAlign: 'center', fontSize: 16, fontWeight: '700', color: INK },
  headerSpacer: { width: 22 },
  hint: {
    fontSize: 12,
    color: Editorial.textCaption,
    paddingHorizontal: PAD,
    marginBottom: 12,
  },
  list: { paddingHorizontal: PAD, paddingBottom: 24, gap: 10 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    padding: 12,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: ink(0.1),
  },
  rowText: { flex: 1, gap: 3 },
  rowName: { fontSize: 14, fontWeight: '600', color: INK },
  rowBrand: { fontSize: 12, color: Editorial.textCaption },
});
