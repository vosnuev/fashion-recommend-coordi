import * as Clipboard from 'expo-clipboard';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';

import { MEMBER_COLORS } from '@/components/closet/shared-space-flow';
import { Icon } from '@/components/icon';
import { ErrorState, LoadingState, SmartImage, useToast } from '@/components/ui';
import { Editorial, ink } from '@/constants/theme';
import { ApiError } from '@/lib/apiClient';
import { joinSharedRoom, previewSharedRoom, type SharedRoomPreview } from '@/lib/wardrobeApi';
import { useAuth } from '@/state/auth';

/** 밝은 아바타 색(1번 노랑) 위에서는 흰 글자가 안 읽힌다 */
const LIGHT_AVATAR_TEXT = '#1C1917';

const GUEST_NAMES = ['참새', '수달', '너구리', '고양이', '두더지', '펭귄', '토끼', '여우'];
const GUEST_NAME_KEY = 'cozy.invite.guestName';

type LoadState = 'loading' | 'ready' | 'notfound' | 'error';

function avatarColor(index: number): string {
  return MEMBER_COLORS[index % MEMBER_COLORS.length];
}

export default function InviteScreen() {
  const params = useLocalSearchParams<{ code?: string }>();
  const router = useRouter();
  const toast = useToast();
  const { width } = useWindowDimensions();
  const [loading, setLoading] = useState(false);
  const [state, setState] = useState<LoadState>('loading');
  const [preview, setPreview] = useState<SharedRoomPreview | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [guestName, setGuestName] = useState('');

  const { isLoggedIn } = useAuth();
  const inviteCode = params.code || '';
  /* 자동 참여는 코드당 한 번만. 재렌더·화면 복귀마다 다시 쏘면 서버에 중복 요청이 간다. */
  const autoJoined = useRef('');

  /* 앱을 아직 안 깐 사람이 링크로 들어온 화면이다 — 코드만 손에 쥐면
     앱을 깔고 "코드로 참여"로 들어갈 수 있어서 복사를 한 번에 되게 둔다. */
  const copyCode = useCallback(async () => {
    if (!inviteCode) return;
    try {
      await Clipboard.setStringAsync(inviteCode);
      toast('참여 코드를 복사했어요', { variant: 'success' });
    } catch {
      toast('복사하지 못했어요 — 길게 눌러 선택해 주세요', { variant: 'error' });
    }
  }, [inviteCode, toast]);

  // 카드 안쪽 폭(컨테이너 padding 24*2, 카드 padding 32*2)에서 2열을 나눈다
  const tileW = Math.max((Math.min(width - 48, 400) - 64 - 10) / 2, 96);

  useEffect(() => {
    if (!inviteCode) return;
    let alive = true;
    previewSharedRoom(inviteCode)
      .then((data) => {
        if (!alive) return;
        setPreview(data);
        setState('ready');
      })
      .catch((err) => {
        if (!alive) return;
        if (err instanceof ApiError && err.status === 404) {
          setState('notfound');
          return;
        }
        console.error('초대장 미리보기 실패:', err);
        setState('error');
      });
    return () => {
      alive = false;
    };
  }, [inviteCode, reloadKey]);

  const retryPreview = useCallback(() => {
    setState('loading');
    setReloadKey((k) => k + 1);
  }, []);

  // 게스트 이름은 화면 표시용 — 서버로 보내지도, 계정에 저장하지도 않는다.
  // 웹 정적 렌더 첫 패스에는 sessionStorage 가 없어 effect 안에서만 만진다.
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof sessionStorage === 'undefined') return;
    const saved = sessionStorage.getItem(GUEST_NAME_KEY);
    const name = saved ?? GUEST_NAMES[Math.floor(Math.random() * GUEST_NAMES.length)];
    if (!saved) sessionStorage.setItem(GUEST_NAME_KEY, name);
    setGuestName(name);
  }, []);

  const handleAcceptInvite = useCallback(async () => {
    if (!inviteCode) {
      toast('참여코드가 유효하지 않습니다.', { variant: 'error' });
      return;
    }

    setLoading(true);
    try {
      const res = await joinSharedRoom(inviteCode);
      toast(
        res.status === 'already_member'
          ? '이미 참여 중인 공유 옷장이에요'
          : '공유 옷장에 참여했어요!',
        { variant: 'success' },
      );
      // closet 탭의 shared 서브탭이 켜지도록 closet으로 리디렉션
      router.replace('/(tabs)/closet?tab=shared');
    } catch (err) {
      console.error('초대 수락 실패:', err);
      toast(err instanceof Error ? err.message : '참여하지 못했어요.', { variant: 'error' });
    } finally {
      setLoading(false);
    }
  }, [inviteCode, router, toast]);

  /* 링크로 들어온 사람은 이미 "들어가겠다"는 의사를 밝힌 것이다.
     로그인돼 있으면 버튼을 한 번 더 누르게 하지 않고 바로 참여시킨다.
     로그인 전이면 아무것도 하지 않는다 — 아래 미리보기를 보고 로그인한 뒤,
     돌아오면 이 효과가 다시 돌아 자동으로 참여된다. */
  useEffect(() => {
    if (!inviteCode || !isLoggedIn) return;
    if (autoJoined.current === inviteCode) return;
    autoJoined.current = inviteCode;
    void handleAcceptInvite();
  }, [inviteCode, isLoggedIn, handleAcceptInvite]);

  const goHome = () => router.replace('/(tabs)/closet');

  /* 로그인 뒤 여기로 되돌아와야 자동 참여가 이어진다. replace 가 아니라 push 로 쌓아
     로그인 화면에서 뒤로 가면 초대장이 그대로 남게 한다. */
  const goLogin = () =>
    router.push(`/login?redirect=${encodeURIComponent(`/invite?code=${inviteCode}`)}`);

  // 코드 없이 들어온 경우 — 조회할 것이 없으니 바로 '없는 초대장'으로 본다
  if (!inviteCode) {
    return (
      <View style={styles.center}>
        <View style={styles.card}>
          <View style={styles.iconContainer}>
            <Icon name="exclamationmark.triangle" tintColor={Editorial.wine} size={32} />
          </View>
          <Text style={styles.title}>유효하지 않은 초대장이에요</Text>
          <Text style={styles.desc}>
            링크에 초대 코드가 없어요.{'\n'}초대한 분께 링크를 다시 받아 주세요.
          </Text>
          <Pressable style={styles.primaryBtn} onPress={goHome}>
            <Text style={styles.primaryBtnText}>홈으로</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  if (state === 'loading') {
    return (
      <View style={styles.center}>
        <LoadingState message="초대장을 여는 중…" />
      </View>
    );
  }

  if (state === 'error') {
    return (
      <ScrollView style={styles.scroll} contentContainerStyle={styles.container}>
        <ErrorState
          title="초대장을 불러오지 못했어요"
          description="네트워크 상태를 확인하고 다시 시도해 주세요."
          onRetry={retryPreview}
        />
        <Pressable style={styles.secondaryBtn} onPress={goHome}>
          <Text style={styles.secondaryBtnText}>홈으로</Text>
        </Pressable>
      </ScrollView>
    );
  }

  if (state === 'notfound' || !preview) {
    return (
      <View style={styles.center}>
        <View style={styles.card}>
          <View style={styles.iconContainer}>
            <Icon name="exclamationmark.triangle" tintColor={Editorial.wine} size={32} />
          </View>
          <Text style={styles.title}>유효하지 않은 초대장이에요</Text>
          <Text style={styles.desc}>
            링크가 잘못되었거나 옷장이 사라졌어요.{'\n'}초대한 분께 링크를 다시 받아 주세요.
          </Text>
          <Pressable style={styles.primaryBtn} onPress={goHome}>
            <Text style={styles.primaryBtnText}>홈으로</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  if (preview.expired) {
    return (
      <View style={styles.center}>
        <View style={styles.card}>
          <View style={styles.iconContainer}>
            <Icon name="exclamationmark.triangle" tintColor={Editorial.wine} size={32} />
          </View>
          <Text style={styles.title}>만료된 초대장이에요</Text>
          <Text style={styles.desc}>초대한 분께 새 링크를 요청해주세요.</Text>
          <Pressable style={styles.primaryBtn} onPress={goHome}>
            <Text style={styles.primaryBtnText}>홈으로</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  const joinBlocked = !preview.can_join || preview.expired;

  return (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.container}>
      <View style={styles.card}>
        <View style={styles.iconContainer}>
          <Icon name="person.2" tintColor={Editorial.ink} size={36} />
        </View>

        <Text style={styles.title}>{preview.title}</Text>
        <Text style={styles.desc}>
          공유 옷장 초대장을 받았어요.{'\n'}
          입장하기 전에 어떤 옷들이 있는지 먼저 구경해 보세요.
        </Text>

        <View style={styles.membersRow}>
          <View style={styles.memberAvatars}>
            {preview.members.map((m, i) => (
              <View
                key={m.index}
                style={[
                  styles.memberDot,
                  i > 0 && styles.memberDotOverlap,
                  { backgroundColor: avatarColor(m.index) },
                ]}>
                <Text
                  style={[styles.memberInitial, m.index === 0 && { color: LIGHT_AVATAR_TEXT }]}>
                  {m.label.slice(0, 1)}
                </Text>
              </View>
            ))}
          </View>
          <Text style={styles.memberCount}>
            {preview.member_count}/{preview.capacity}명
          </Text>
        </View>

        {guestName ? <Text style={styles.guestNote}>손님({guestName})으로 구경 중</Text> : null}

        <View style={styles.codeBox}>
          <Text style={styles.codeLabel}>참여 코드 (눌러서 복사)</Text>
          <Pressable onPress={copyCode} hitSlop={8}>
            <Text style={styles.codeText} selectable>
              {inviteCode || 'CODE_MISSING'}
            </Text>
          </Pressable>
        </View>

        {preview.items.length > 0 ? (
          <>
            <Text style={styles.sectionTitle}>옷장 미리보기</Text>
            <View style={styles.grid}>
              {/* 읽기 전용 — 미리보기 아이템에는 id 가 없어 상세로 들어갈 수 없다 */}
              {preview.items.map((it, i) => (
                <View key={`${it.owner_index}-${i}`} style={[styles.tile, { width: tileW }]}>
                  <View>
                    <SmartImage uri={it.image_url} width="100%" aspectRatio={1} radius={12} />
                    <View
                      style={[styles.ownerBadge, { backgroundColor: avatarColor(it.owner_index) }]}>
                      <Text
                        style={[
                          styles.ownerText,
                          it.owner_index === 0 && { color: LIGHT_AVATAR_TEXT },
                        ]}>
                        {it.owner_label}
                      </Text>
                    </View>
                  </View>
                  <Text style={styles.tileName} numberOfLines={1}>
                    {it.item_name || it.category_large}
                  </Text>
                </View>
              ))}
            </View>
          </>
        ) : null}

        {/* 로그인 전에는 참여 API 가 401 이라 눌러도 실패한다 — 로그인으로 먼저 보낸다.
            로그인을 마치고 이 화면으로 돌아오면 위 useEffect 가 자동으로 참여시킨다. */}
        <Pressable
          style={[styles.primaryBtn, (loading || joinBlocked) && styles.disabledBtn]}
          onPress={isLoggedIn ? handleAcceptInvite : goLogin}
          disabled={loading || joinBlocked}
        >
          <Text style={styles.primaryBtnText}>
            {loading
              ? '참여하는 중...'
              : isLoggedIn
                ? '초대 수락하고 입장하기'
                : '로그인하고 참여하기'}
          </Text>
        </Pressable>
        {!preview.can_join ? <Text style={styles.blockedHint}>정원이 가득 찼어요</Text> : null}

        <Pressable style={styles.secondaryBtn} onPress={goHome}>
          <Text style={styles.secondaryBtnText}>취소하고 홈으로</Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: {
    flex: 1,
    backgroundColor: '#F9FAFB',
  },
  container: {
    flexGrow: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  center: {
    flex: 1,
    backgroundColor: '#F9FAFB',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 24,
    padding: 32,
    width: '100%',
    maxWidth: 400,
    alignItems: 'center',
    // 그림자 효과 (Premium Card Feel)
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.05,
    shadowRadius: 16,
    elevation: 4,
  },
  iconContainer: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: '#F3F4F6',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 20,
  },
  title: {
    fontSize: 22,
    fontWeight: '700',
    color: Editorial.ink,
    marginBottom: 12,
    textAlign: 'center',
  },
  desc: {
    fontSize: 14,
    color: ink(0.56),
    lineHeight: 20,
    textAlign: 'center',
    marginBottom: 20,
  },
  membersRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
  },
  memberAvatars: { flexDirection: 'row' },
  memberDot: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  memberDotOverlap: { marginLeft: -8 },
  memberInitial: { fontSize: 11, fontWeight: '600', color: '#FFFFFF' },
  memberCount: { fontSize: 13, color: ink(0.5), fontWeight: '500' },
  guestNote: {
    fontSize: 12,
    color: ink(0.4),
    marginBottom: 16,
  },
  codeBox: {
    backgroundColor: '#F9FAFB',
    borderWidth: 1,
    borderColor: '#E5E7EB',
    borderRadius: 16,
    paddingVertical: 16,
    paddingHorizontal: 24,
    width: '100%',
    alignItems: 'center',
    marginTop: 12,
    marginBottom: 24,
  },
  codeLabel: {
    fontSize: 12,
    color: ink(0.4),
    marginBottom: 4,
    fontWeight: '500',
  },
  codeText: {
    fontSize: 24,
    fontWeight: '800',
    color: Editorial.ink,
    letterSpacing: 2,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: '600',
    color: ink(0.5),
    alignSelf: 'flex-start',
    marginBottom: 10,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    width: '100%',
    marginBottom: 24,
  },
  tile: { gap: 6 },
  ownerBadge: {
    position: 'absolute',
    top: 6,
    left: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  ownerText: { fontSize: 10, fontWeight: '700', color: '#FFFFFF' },
  tileName: { fontSize: 12, color: ink(0.7) },
  primaryBtn: {
    backgroundColor: Editorial.ink,
    borderRadius: 16,
    height: 52,
    width: '100%',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 12,
  },
  disabledBtn: {
    backgroundColor: ink(0.3),
  },
  primaryBtnText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '600',
  },
  blockedHint: {
    fontSize: 12,
    color: Editorial.wine,
    marginBottom: 8,
  },
  secondaryBtn: {
    height: 48,
    width: '100%',
    alignItems: 'center',
    justifyContent: 'center',
  },
  secondaryBtnText: {
    color: ink(0.5),
    fontSize: 14,
    fontWeight: '500',
  },
});
