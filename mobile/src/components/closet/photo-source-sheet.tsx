import { router } from 'expo-router';
import { useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { Icon, type IconName } from '@/components/icon';
import { useToast } from '@/components/ui';
import { ContentMax, Editorial, ink, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { pickFromAlbum, pickFromCamera } from '@/lib/pickItemPhoto';
import { draftItem } from '@/state/draft-item';

const INK = Editorial.ink;

type SourceKey = 'album' | 'camera' | 'web' | 'library';

/* 앨범·카메라는 내 사진, Web 은 쇼핑몰에서 가져오기, 라이브러리는 준비된 옷에서 고르기.
   라이브러리를 넣은 이유 — 사진이 없어도 옷장을 채워볼 수 있는 유일한 길이다.
   (화면은 진작 있었는데 아무 데서도 열어주지 않아 닿을 수 없었다) */
const ALL_SOURCES: { key: SourceKey; label: string; icon: IconName; hint: string }[] = [
  { key: 'album', label: '앨범', icon: 'photo.on.rectangle', hint: '갤러리에서 선택' },
  { key: 'camera', label: '카메라', icon: 'camera', hint: '직접 촬영' },
  { key: 'web', label: 'Web', icon: 'globe', hint: '쇼핑몰에서' },
  { key: 'library', label: '라이브러리', icon: 'book', hint: '준비된 옷에서' },
];

/* Web(쇼핑몰에서 가져오기)은 인앱 브라우저가 있어야 동작한다. 브라우저에서는 쇼핑몰 페이지를
   iframe 으로 띄우는 것도, 그 안을 읽는 것도 막혀 있어 원리적으로 불가능하다.
   → 웹에서는 눌러봐야 안내만 뜨므로 아예 감춘다. */
const SOURCES = ALL_SOURCES.filter((s) => !(s.key === 'web' && Platform.OS === 'web'));

/* 넷이면 2×2, 셋이면 한 줄. (48%+gap / 31%+gap) */
const TILE_BASIS = SOURCES.length === 3 ? ('31%' as const) : ('48%' as const);

/**
 * 사진 가져올 곳 고르기.
 *
 * 화면을 새로 띄우지 않고 **아래에서 조금 올라오는 시트**로 둔다 — 고르는 일이 한 번의
 * 선택이라, 화면을 옮겨 갔다 돌아오는 왕복이 과했다.
 * 고르면 사진을 draft 에 담고 등록 화면(/item-add)으로 넘긴다.
 */
export function PhotoSourceSheet({
  visible,
  onClose,
}: {
  visible: boolean;
  onClose: () => void;
}) {
  const { isDesktop } = useBreakpoint();
  const toast = useToast();
  const [active, setActive] = useState<SourceKey | null>(null);
  const [loading, setLoading] = useState(false);

  const handlePick = async (key: SourceKey) => {
    setActive(key);

    if (key === 'web' || key === 'library') {
      onClose();
      router.push(key === 'web' ? '/import' : '/item-add-library');
      setActive(null);
      return;
    }

    setLoading(true);
    try {
      const uri = key === 'album' ? await pickFromAlbum() : await pickFromCamera();
      if (!uri) return;
      draftItem.setPhoto(uri);
      onClose();
      router.push('/item-add');
    } catch {
      toast('사진을 불러오지 못했어요', { variant: 'error' });
    } finally {
      setLoading(false);
      setActive(null);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable
          style={[styles.sheet, isDesktop && styles.sheetDesktop]}
          onPress={(e) => e.stopPropagation()}>
          <View style={styles.handle} />
          <Text style={styles.title}>사진 추가</Text>

          {/* 앱은 네 갈래라 2×2 (한 줄에 넷을 넣으면 좁은 폰에서 '라이브러리'가 잘린다).
              웹은 Web 타일이 빠져 셋이므로 한 줄에 담는다. */}
          <View style={styles.grid}>
            {SOURCES.map((src) => {
              const on = active === src.key;
              return (
                <Pressable
                  key={src.key}
                  style={[styles.tile, { flexBasis: TILE_BASIS }, on && styles.tileOn]}
                  onPress={() => handlePick(src.key)}
                  disabled={loading}>
                  <Icon name={src.icon} tintColor={on ? '#fff' : ink(0.55)} size={24} />
                  <Text style={[styles.tileLabel, on && styles.tileLabelOn]}>{src.label}</Text>
                  <Text style={[styles.tileHint, on && styles.tileHintOn]} numberOfLines={1}>
                    {src.hint}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          {loading ? (
            <View style={styles.loadingRow}>
              <ActivityIndicator color={INK} />
              <Text style={styles.loadingText}>사진 불러오는 중…</Text>
            </View>
          ) : null}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(28,25,23,0.35)' },
  sheet: {
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingBottom: 32,
  },
  /* 데스크톱에서도 아래에서 올라오되 창 폭을 다 쓰지는 않는다. */
  sheetDesktop: {
    width: '100%',
    maxWidth: ContentMax.card,
    marginHorizontal: 'auto',
    borderBottomLeftRadius: 20,
    borderBottomRightRadius: 20,
    marginBottom: 24,
  },
  handle: {
    alignSelf: 'center',
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: ink(0.12),
    marginTop: 10,
    marginBottom: 16,
  },
  title: { fontSize: Type.lead, fontWeight: '700', color: INK, marginBottom: 14 },

  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  tile: {
    /* 폭은 flexBasis 로 나눠 갖는다(TILE_BASIS). 직접 계산하면 좁은 창에서 넘쳐 줄이 어긋난다.
       높이는 고정 — 비율로 두면 폭이 넓은 데스크톱에서 타일이 쓸데없이 커진다. */
    flexGrow: 1,
    minWidth: 0,
    height: 96,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: ink(0.12),
    backgroundColor: Editorial.surface,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingHorizontal: 6,
  },
  tileOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  tileLabel: { fontSize: Type.footnote, fontWeight: '600', color: INK },
  tileLabelOn: { color: '#fff' },
  tileHint: { fontSize: Type.micro, color: Editorial.textCaption },
  tileHintOn: { color: 'rgba(255,255,255,0.72)' },

  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    marginTop: 20,
  },
  loadingText: { fontSize: Type.caption, color: Editorial.textCaption },
});
