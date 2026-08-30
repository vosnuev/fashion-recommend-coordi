import { Modal, Platform, Pressable, Share, StyleSheet, Text, View } from 'react-native';

import { Icon } from '@/components/icon';
import { SmartImage, useToast } from '@/components/ui';
import { ContentMax, Editorial, ink, Type } from '@/constants/theme';
import { formatDateLabel, lookShareUrl, type CalendarEntry } from '@/state/calendar';

const INK = Editorial.ink;

/** 데모 기준 함께 쓰는 옷장 멤버 — 옷장 스페이스가 붙으면 space.members 로 대체된다 */
const FRIEND_NAMES = ['지민', '서연', '민준'];

async function copyToClipboard(text: string): Promise<boolean> {
  if (Platform.OS === 'web' && typeof navigator !== 'undefined' && navigator.clipboard) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  return false;
}

/**
 * 착장 기록 공유 — 두 갈래를 한 시트에 둔다.
 *  1) 함께 쓰는 옷장 친구에게 공개 (앱 안, 토글)
 *  2) 링크로 내보내기 (인스타·카톡 등 앱 밖, OS 공유 시트)
 * 공개 토글은 즉시 반영된다. 공유는 되돌릴 수 있어야 해서 저장과 분리했다.
 */
export function ShareLookSheet({
  entry,
  visible,
  onClose,
  onToggleShared,
}: {
  entry: CalendarEntry;
  visible: boolean;
  onClose: () => void;
  onToggleShared: (shared: boolean) => void;
}) {
  const toast = useToast();
  const link = lookShareUrl(entry.shareCode);
  const message = `[cozy] ${formatDateLabel(entry.date)}의 착장\n${link}`;

  const copy = async () => {
    if (await copyToClipboard(link)) {
      toast('링크를 복사했어요', { variant: 'success' });
      return;
    }
    try {
      await Share.share({ message: link });
    } catch {
      /* 사용자가 취소 */
    }
  };

  const shareExternal = async () => {
    try {
      await Share.share({ message, title: '오늘의 착장' });
    } catch {
      /* 사용자가 취소 */
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
          <View style={styles.handle} />

          <View style={styles.preview}>
            <SmartImage uri={entry.photo} width={52} height={64} radius={10} />
            <View style={styles.previewBody}>
              <Text style={styles.previewDate}>{formatDateLabel(entry.date)}</Text>
              <Text style={styles.previewMeta}>
                {entry.items.length > 0 ? `옷 ${entry.items.length}개` : '사진 기록'}
                {entry.tags.length > 0 ? ` · ${entry.tags.map((t) => `#${t}`).join(' ')}` : ''}
              </Text>
            </View>
          </View>

          <Pressable style={styles.friendRow} onPress={() => onToggleShared(!entry.shared)}>
            <View style={styles.friendIcon}>
              <Icon name="person.2" tintColor={INK} size={17} />
            </View>
            <View style={styles.friendBody}>
              <Text style={styles.friendTitle}>함께 쓰는 옷장 친구에게 공개</Text>
              <Text style={styles.friendDesc}>
                {entry.shared
                  ? `${FRIEND_NAMES.join('·')}님이 이 기록을 볼 수 있어요`
                  : '지금은 나만 볼 수 있어요'}
              </Text>
            </View>
            <View style={[styles.switch, entry.shared && styles.switchOn]}>
              <View style={[styles.knob, entry.shared && styles.knobOn]} />
            </View>
          </Pressable>

          <Text style={styles.sectionLabel}>링크로 공유</Text>
          <View style={styles.linkBox}>
            <Text style={styles.linkText} numberOfLines={1}>
              {link}
            </Text>
            <Pressable style={styles.linkCopyBtn} onPress={copy} hitSlop={6}>
              <Icon name="link" tintColor={INK} size={16} />
            </Pressable>
          </View>

          <Pressable style={styles.primaryBtn} onPress={shareExternal}>
            <Icon name="square.and.arrow.up" tintColor="#fff" size={17} />
            <Text style={styles.primaryBtnText}>인스타그램·카톡으로 공유</Text>
          </Pressable>
          <Text style={styles.hint}>링크를 받은 사람은 로그인 없이 이 착장만 볼 수 있어요</Text>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: ink(0.35) },
  sheet: {
    width: '100%',
    maxWidth: ContentMax.narrow,
    alignSelf: 'center',
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingBottom: 32,
  },
  handle: {
    alignSelf: 'center',
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: ink(0.12),
    marginTop: 10,
    marginBottom: 18,
  },

  preview: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 18 },
  previewBody: { flex: 1, gap: 4 },
  previewDate: { fontSize: Type.body, fontWeight: '700', color: INK },
  previewMeta: { fontSize: Type.caption, color: Editorial.textCaption },

  friendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  friendIcon: {
    width: 34,
    height: 34,
    borderRadius: 17,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  friendBody: { flex: 1, gap: 3 },
  friendTitle: { fontSize: Type.footnote, fontWeight: '600', color: INK },
  friendDesc: { fontSize: Type.micro, color: Editorial.textCaption },
  switch: {
    width: 44,
    height: 26,
    borderRadius: 13,
    borderWidth: 1,
    borderColor: Editorial.line,
    padding: 2,
    justifyContent: 'center',
  },
  switchOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  knob: {
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: ink(0.2),
  },
  knobOn: { backgroundColor: '#fff', alignSelf: 'flex-end' },

  sectionLabel: {
    fontSize: Type.caption,
    fontWeight: '600',
    color: Editorial.textCaption,
    marginTop: 22,
    marginBottom: 8,
  },
  linkBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    height: 46,
    paddingHorizontal: 14,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  linkText: { flex: 1, fontSize: Type.caption, color: Editorial.textSoft },
  linkCopyBtn: { padding: 4 },

  primaryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    height: 48,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    marginTop: 12,
  },
  primaryBtnText: { fontSize: Type.body, fontWeight: '600', color: '#fff' },
  hint: {
    fontSize: Type.micro,
    color: Editorial.textMuted,
    textAlign: 'center',
    marginTop: 12,
  },
});
