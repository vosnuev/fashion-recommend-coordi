import { Icon } from '@/components/icon';
import { useToast } from '@/components/ui';
import { Editorial, ink, Type } from '@/constants/theme';
import { useEffect, useState } from 'react';
import { INVITE_BASE_URL } from '@/constants/config';
import {
  copyText,
  inviteMessage,
  openShareSheet,
  preloadKakaoWebSdk,
  shareInviteViaKakao,
} from '@/lib/kakaoShare';
import { Modal, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

export type SharedSpace = {
  id: string;
  name: string;
  inviteCode: string;
  inviteCodeExpiresAt: string | null;
  members: string[];
  role: 'owner' | 'member';
};

/**
 * 이 주소를 **남에게 보내도 열리는가**.
 *
 * localhost·127.0.0.1·사설 IP(10./192.168./172.16~31.)는 보내는 사람 컴퓨터에서만
 * 열린다. 받는 사람이 누르면 자기 기기의 같은 주소로 가서 아무것도 안 뜬다 —
 * 실제로 `http://localhost:8081/invite?code=...` 링크를 보내 겪은 문제다.
 */
function isShareableOrigin(origin: string): boolean {
  const host = origin.replace(/^https?:\/\//, '').split(':')[0];
  if (!host) return false;
  if (host === 'localhost' || host === '0.0.0.0' || host.startsWith('127.')) return false;
  if (host === '[::1]' || host === '::1') return false;
  if (host.startsWith('10.') || host.startsWith('192.168.')) return false;
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(host)) return false;
  return true;
}

function makeInviteLink(code: string) {
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    const origin = window.location.origin;
    if (isShareableOrigin(origin)) return `${origin}/invite?code=${code}`;
  }
  return `${INVITE_BASE_URL}/invite?code=${code}`;
}

/** 스페이스가 없을 때 — 만들기 / 초대 링크로 참여 */
export function SharedSpaceOnboarding({
  onCreate,
  onJoin,
}: {
  onCreate: () => void;
  onJoin: () => void;
}) {
  return (
    <View style={styles.onboarding}>
      <View style={styles.onboardingIcon}>
        <Icon name="person.2" tintColor={ink(0.32)} size={28} />
      </View>
      <Text style={styles.onboardingTitle}>함께 쓰는 옷장</Text>
      <Text style={styles.onboardingDesc}>
        카톡·SNS·링크로 친구를 초대하고{'\n'}같은 공간에서 옷장을 공유해 보세요.
      </Text>
      <Pressable style={styles.primaryBtn} onPress={onCreate}>
        <Text style={styles.primaryBtnText}>옷장 만들기</Text>
      </Pressable>
      <Pressable style={styles.secondaryBtn} onPress={onJoin}>
        <Text style={styles.secondaryBtnText}>초대 링크로 참여하기</Text>
      </Pressable>
    </View>
  );
}

/* '아직 혼자예요' 초대 배너(SharedSpaceInviteBanner)는 2026-08-16 제거했다.
   멤버 줄에 [+초대] 버튼과 참여코드 입력칸이 이미 있어 같은 말이 세 번 나왔다. */

/** 가입 순서(index) 기반 고정 색. 초대장 화면도 같은 색을 써야 해서 여기서 내보낸다. */
export const MEMBER_COLORS = [
  '#FFD54F', // 노랑
  '#4FC3F7', // 하늘
  '#81C784', // 연두
  '#F06292', // 핑크
  '#BA68C8', // 보라
  '#FFB74D', // 주황
];

export function getAvatarColor(name: string): string {
  // 하위 호환용 (혹시 다른 곳에서 사용 시)
  return MEMBER_COLORS[0];
}

/** 참여코드는 대소문자·앞뒤 공백을 가리지 않게 받는다 (카톡에서 복사하면 공백이 붙어 온다). */
function normalizeJoinCode(raw: string): string {
  return raw.trim().toUpperCase();
}

/** 멤버 아바타 + 초대 버튼 + 참여코드 입력 */
export function SharedSpaceMembers({
  space,
  onInvite,
  onJoin,
  onRefreshInviteCode,
}: {
  space: SharedSpace;
  onInvite: () => void;
  /** 없으면 입력칸을 숨긴다 — 참여 처리를 못 하는 화면에서 입력만 받는 건 거짓말이 된다. */
  onJoin?: (code: string) => Promise<boolean> | boolean;
  onRefreshInviteCode?: () => void;
}) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [inviteCodeExpired, setInviteCodeExpired] = useState(false);
  const trimmed = normalizeJoinCode(code);
  const canSubmit = Boolean(onJoin) && trimmed.length > 0 && !busy;

  useEffect(() => {
    let expirationTimer: ReturnType<typeof setTimeout> | undefined;
    const checkExpiration = () => {
      const expiresAt = space.inviteCodeExpiresAt
        ? new Date(space.inviteCodeExpiresAt).getTime()
        : Number.NaN;
      if (!Number.isFinite(expiresAt)) {
        setInviteCodeExpired(false);
        return;
      }

      const remainingMs = expiresAt - Date.now();
      if (remainingMs <= 0) {
        setInviteCodeExpired(true);
        return;
      }
      setInviteCodeExpired(false);
      /* JS 타이머 최대 범위를 넘는 만료 시각도 단계적으로 다시 확인한다. */
      expirationTimer = setTimeout(checkExpiration, Math.min(remainingMs + 100, 2_147_483_647));
    };

    /* effect 본문에서 동기 setState를 하지 않아 첫 렌더와 서버 만료 시각 판정을 분리한다. */
    const initialTimer = setTimeout(checkExpiration, 0);
    return () => {
      clearTimeout(initialTimer);
      if (expirationTimer) clearTimeout(expirationTimer);
    };
  }, [space.inviteCodeExpiresAt]);

  const submit = async () => {
    if (!onJoin || !canSubmit) return;
    setBusy(true);
    try {
      /* 실패 사유(정원 초과·만료·없는 코드)는 onJoin 이 이미 서버 문구로 띄운다.
         여기서 덧씌우면 정원이 꽉 찬 경우까지 코드가 틀린 것처럼 보인다.
         성공했을 때만 비운다 — 오타면 고쳐서 다시 넣을 수 있어야 한다. */
      if (await onJoin(trimmed)) setCode('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.membersBlock}>
      <View style={styles.membersRow}>
        <View style={styles.memberAvatars}>
          {space.members.map((member, i) => {
            const ch = member.slice(0, 1);
            const bgColor = MEMBER_COLORS[i % MEMBER_COLORS.length];
            return (
              <View
                key={`${member}-${i}`}
                style={[
                  styles.memberDot,
                  i > 0 && styles.memberDotOverlap,
                  { backgroundColor: bgColor },
                ]}>
                <Text style={styles.memberInitial}>{ch}</Text>
              </View>
            );
          })}
        </View>
        <Text style={styles.memberCount}>{space.members.length}명</Text>
        <View style={[styles.roleBadge, space.role === 'owner' && styles.roleBadgeOwner]}>
          <Text style={[styles.roleBadgeText, space.role === 'owner' && styles.roleBadgeTextOwner]}>
            {space.role === 'owner' ? '방장' : '멤버'}
          </Text>
        </View>
        <Pressable style={styles.inviteChip} onPress={onInvite} hitSlop={6}>
          <Icon name="plus" tintColor={Editorial.ink} size={14} />
          <Text style={styles.inviteChipText}>초대</Text>
        </Pressable>

        {onJoin ? (
          <View style={styles.joinInline}>
            <TextInput
              style={styles.joinInlineInput}
              placeholder="참여코드"
              placeholderTextColor={ink(0.3)}
              value={code}
              onChangeText={setCode}
              autoCapitalize="characters"
              autoCorrect={false}
              maxLength={6} // 발급 코드가 6자리 고정 (makeInviteCode·서버 모두)
              editable={!busy}
              returnKeyType="go"
              onSubmitEditing={submit}
              accessibilityLabel="참여코드 입력"
            />
            <Pressable
              style={[styles.joinInlineBtn, !canSubmit && styles.joinInlineBtnOff]}
              onPress={submit}
              disabled={!canSubmit}
              hitSlop={6}
              accessibilityLabel="참여코드로 공유 옷장 참여">
              <Icon name="arrow.right" tintColor={Editorial.surface} size={13} />
            </Pressable>
          </View>
        ) : null}
      </View>

      {space.role === 'owner' && inviteCodeExpired && onRefreshInviteCode ? (
        <View style={styles.expiredCodeNotice}>
          <View style={styles.expiredCodeCopy}>
            <Text style={styles.expiredCodeTitle}>초대 코드가 만료됐어요</Text>
            <Text style={styles.expiredCodeDescription}>새 코드를 발급하면 바로 초대할 수 있어요.</Text>
          </View>
          <Pressable
            style={styles.expiredCodeButton}
            onPress={onRefreshInviteCode}
            accessibilityRole="button"
            accessibilityLabel="만료된 초대 코드 새로 발급">
            <Text style={styles.expiredCodeButtonText}>새 코드 발급</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

/** 초대 링크 공유 시트 */
export function SharedSpaceInviteSheet({
  space,
  visible,
  onClose,
}: {
  space: SharedSpace;
  visible: boolean;
  onClose: () => void;
}) {
  const toast = useToast();
  const link = makeInviteLink(space.inviteCode);

  const invite = { roomName: space.name, code: space.inviteCode, link };

  /* 시트가 열리는 순간 카카오 SDK를 미리 받아 둔다.
     클릭한 뒤에 받으면 그 사이 사용자 제스처가 끝나 공유창(팝업)이 차단된다. */
  useEffect(() => {
    if (visible) preloadKakaoWebSdk();
  }, [visible]);

  const shareLink = async (via: 'kakao' | 'sns') => {
    if (via === 'kakao') {
      // HTTPS 웹은 카카오 JS 공유창, 네이티브는 카카오톡 SDK를 연다.
      const result = await shareInviteViaKakao(invite);
      if (result === 'kakao') {
        toast('카카오톡 공유창을 열었어요', { variant: 'success' });
      } else if (result === 'share-sheet') {
        toast('공유 앱을 골라 주세요 — 초대 문구는 복사해 뒀어요', { variant: 'success' });
      } else if (result === 'clipboard') {
        toast('초대 문구를 복사했어요. 카카오톡 대화방에 붙여넣어 주세요', {
          variant: 'success',
        });
      } else if (result === 'no-key') {
        // 설정 누락은 사용자가 아무리 다시 눌러도 안 된다 — 원인을 그대로 말한다.
        toast('카카오 공유 설정이 없어요 (EXPO_PUBLIC_KAKAO_JAVASCRIPT_KEY)', {
          variant: 'error',
        });
      } else {
        toast('공유하지 못했어요. 아래 참여 코드를 눌러 복사해 주세요', { variant: 'error' });
      }
      return;
    }

    /* 다른 앱으로 공유해도 참여 코드가 빠지지 않도록 카카오와 같은 문구를 쓴다.
       공유 시트를 못 여는 환경(웹 Share API 미지원·비보안 컨텍스트)에서는
       아무 일도 안 일어난 것처럼 보이므로 복사로 대신하고 그렇다고 말해 준다. */
    const message = inviteMessage(invite);
    if (await openShareSheet(message, `${space.name} 초대`)) return;

    if (await copyText(message)) {
      toast('공유 앱을 열 수 없어 초대 문구를 복사했어요', { variant: 'success' });
    } else {
      toast('공유하지 못했어요. 아래 참여 코드를 눌러 복사해 주세요', { variant: 'error' });
    }
  };

  /* 실패해도 반드시 말해 준다. 이전엔 성공했을 때만 토스트를 띄워서,
     복사가 막히면 "눌러도 아무 일도 안 일어남"으로 보였다. */
  const copyCode = async () => {
    if (await copyText(space.inviteCode)) {
      toast('참여 코드를 복사했어요', { variant: 'success' });
    } else {
      toast('복사가 막혔어요 — 코드를 길게 눌러 직접 복사해 주세요', { variant: 'error' });
    }
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.sheetBackdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
          <View style={styles.sheetHeader}>
            <View style={styles.sheetHeaderText}>
              <Text style={styles.sheetTitle}>친구 초대하기</Text>
              <Text style={styles.sheetSubtitle}>{space.name}</Text>
            </View>
            {/* 배경을 눌러도 닫히지만, 모달 안에서 닫을 곳이 없으면 갇힌 느낌이 든다 */}
            <Pressable onPress={onClose} hitSlop={12} accessibilityLabel="닫기">
              <Icon name="xmark" tintColor={ink(0.5)} size={18} />
            </Pressable>
          </View>

          {/* URL 은 노출하지 않는다 — 참여는 6자리 코드로만 받기로 했다.
              링크 자체는 카카오 카드 버튼용으로 내부에서만 쓴다. */}
          <Text style={styles.codeLabel}>참여 코드</Text>
          <View style={styles.codeRow}>
            {/* 복사가 막히는 환경(비보안 컨텍스트·권한 거부)이 있어 코드 자체도
                눌러서 복사되고, 손으로 드래그 선택도 되게 둔다. */}
            <Text
              style={styles.codeValue}
              selectable
              onPress={copyCode}
              accessibilityLabel={`참여 코드 ${space.inviteCode}, 눌러서 복사`}>
              {space.inviteCode}
            </Text>
            <Pressable style={styles.codeCopyBtn} onPress={copyCode} hitSlop={8}>
              <Icon name="link" tintColor={Editorial.ink} size={14} />
              <Text style={styles.codeCopyText}>코드복사</Text>
            </Pressable>
          </View>
          <Text style={styles.codeHint}>친구가 이 코드를 입력하면 바로 들어와요</Text>

          <Pressable style={styles.kakaoBtn} onPress={() => shareLink('kakao')}>
            <Text style={styles.kakaoBtnText}>카카오톡으로 공유</Text>
          </Pressable>
          <Pressable style={styles.snsBtn} onPress={() => shareLink('sns')}>
            <Icon name="square.and.arrow.up" tintColor={Editorial.ink} size={18} />
            <Text style={styles.snsBtnText}>다른 앱으로 공유</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

/** 초대 코드 입력으로 참여 */
export function SharedSpaceJoinSheet({
  visible,
  onClose,
  onJoin,
}: {
  visible: boolean;
  onClose: () => void;
  onJoin: (code: string) => Promise<boolean> | boolean;
}) {
  const [code, setCode] = useState('');
  const toast = useToast();

  const submit = async () => {
    const trimmed = code.trim().toUpperCase();
    if (!trimmed) {
      toast('초대 코드를 입력해 주세요', { variant: 'error' });
      return;
    }
    const ok = await onJoin(trimmed);
    if (ok) {
      setCode('');
      onClose();
      return;
    }
    /* 실패 사유는 onJoin 이 이미 서버 문구로 띄웠다(정원 초과·만료·없는 코드).
       여기서 '유효하지 않은 초대 코드'를 덧씌우면 정원이 꽉 찬 경우까지
       코드가 틀린 것처럼 보여서 사용자가 엉뚱한 곳을 고치게 된다.
       시트도 닫지 않는다 — 코드를 고쳐 다시 넣을 수 있어야 한다. */
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.sheetBackdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
          <Text style={styles.sheetTitle}>초대 링크로 참여</Text>
          <Text style={styles.sheetSubtitle}>친구가 보낸 링크의 코드를 입력하세요</Text>

          <TextInput
            style={styles.codeInput}
            placeholder="예: COZY2024"
            placeholderTextColor={ink(0.3)}
            value={code}
            onChangeText={setCode}
            autoCapitalize="characters"
            autoCorrect={false}
          />

          <Pressable style={styles.primaryBtn} onPress={submit}>
            <Text style={styles.primaryBtnText}>참여하기</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function makeInviteCode(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let code = '';
  for (let i = 0; i < 6; i++) {
    code += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return code;
}

export function createSharedSpace(name = '우리 옷장'): SharedSpace {
  return {
    id: `space-${Date.now()}`,
    name,
    inviteCode: makeInviteCode(),
    inviteCodeExpiresAt: null,
    members: ['나'],
    role: 'owner',
  };
}

const styles = StyleSheet.create({
  onboarding: {
    width: '100%',
    alignItems: 'center',
    paddingTop: 32,
    paddingBottom: 24,
  },
  onboardingIcon: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: Editorial.surfaceSoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 20,
  },
  onboardingTitle: {
    fontSize: Type.lead,
    fontWeight: '600',
    color: Editorial.ink,
    textAlign: 'center',
  },
  onboardingDesc: {
    fontSize: Type.footnote,
    color: Editorial.textCaption,
    textAlign: 'center',
    marginTop: 8,
    lineHeight: 21,
  },
  primaryBtn: {
    marginTop: 28,
    width: '100%',
    height: 48,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryBtnText: { fontSize: Type.footnote, fontWeight: '600', color: '#fff' },
  secondaryBtn: {
    marginTop: 12,
    width: '100%',
    height: 48,
    borderRadius: 999,
    backgroundColor: Editorial.surface,
    borderWidth: 1, borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  secondaryBtnText: { fontSize: Type.footnote, fontWeight: '600', color: Editorial.textSoft },

  membersBlock: { marginBottom: 12 },
  membersRow: {
    flexDirection: 'row',
    alignItems: 'center',
    /* 정원 상한 6명이면 아바타 줄만 118px이라 좁은 기기에서 참여코드 입력칸이 밀려 잘린다.
       wrap 을 주면 자리가 모자랄 때 입력칸이 아랫줄로 내려가 온전히 보인다. */
    flexWrap: 'wrap',
    paddingHorizontal: 20,
    gap: 8,
  },
  memberAvatars: { flexDirection: 'row' },
  memberDot: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: Editorial.ink,
    alignItems: 'center',
    justifyContent: 'center',
  },
  memberDotOverlap: { marginLeft: -8 },
  memberInitial: { fontSize: 11, fontWeight: '600', color: '#fff' },
  memberCount: { fontSize: Type.micro, color: Editorial.textCaption, flex: 1 },
  roleBadge: {
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: 999,
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  roleBadgeOwner: {
    backgroundColor: Editorial.selected,
    borderColor: Editorial.selected,
  },
  roleBadgeText: { fontSize: Type.micro, fontWeight: '600', color: Editorial.textCaption },
  roleBadgeTextOwner: { color: Editorial.white },
  inviteChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: Editorial.surface,
  },
  inviteChipText: { fontSize: Type.micro, fontWeight: '600', color: Editorial.textSoft },

  /* 참여코드는 6자리 고정이라 입력칸도 딱 6자리만큼만 준다.
     flex 로 늘리면 빈 칸이 남아 옆의 [초대] 칩보다 커 보인다. */
  joinInline: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingLeft: 8,
    paddingRight: 4,
    paddingVertical: 3,
    borderRadius: 999,
    backgroundColor: Editorial.surface,
  },
  joinInlineInput: {
    width: 62, // 12px 반각 6자 + letterSpacing 1
    paddingVertical: 0,
    fontSize: Type.micro,
    fontWeight: '600',
    letterSpacing: 1,
    color: Editorial.ink,
    /* 웹에서 TextInput 은 기본 포커스 링이 붙어 칩 모양이 깨진다. */
    ...(Platform.OS === 'web' ? { outlineStyle: 'none' as never } : null),
  },
  joinInlineBtn: {
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.ink,
  },
  joinInlineBtnOff: { opacity: 0.25 },
  expiredCodeNotice: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginHorizontal: 20,
    marginTop: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: Editorial.line,
    backgroundColor: Editorial.surface,
  },
  expiredCodeCopy: { flex: 1 },
  expiredCodeTitle: { fontSize: Type.micro, fontWeight: '700', color: Editorial.ink },
  expiredCodeDescription: { marginTop: 3, fontSize: Type.micro, color: Editorial.textCaption },
  expiredCodeButton: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: Editorial.ink,
  },
  expiredCodeButtonText: { fontSize: Type.micro, fontWeight: '600', color: Editorial.surface },

  sheetBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(28,25,23,0.45)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 24,
    paddingTop: 24,
    paddingBottom: 36,
  },
  sheetHeader: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 20 },
  sheetHeaderText: { flex: 1 },
  sheetTitle: { fontSize: Type.label, fontWeight: '600', color: Editorial.ink },
  sheetSubtitle: { fontSize: Type.footnote, color: Editorial.textCaption, marginTop: 4 },
  /* 참여는 6자리 코드로만 받는다 — 코드가 이 시트의 주인공이라 크게 키웠다.
     (URL 을 보여주던 linkBox 계열 스타일은 링크 노출을 걷어내면서 함께 삭제) */
  codeLabel: { fontSize: Type.micro, color: Editorial.textCaption, marginTop: 4 },
  /* 코드와 복사 버튼을 한 줄에 둔다 — 코드 오른쪽이 비어 있었다 */
  codeRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 6 },
  codeValue: {
    flex: 1,
    fontSize: 30,
    fontWeight: '700',
    letterSpacing: 6,
    color: Editorial.ink,
  },
  codeCopyBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    height: 36,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Editorial.line,
    backgroundColor: Editorial.surfaceSoft,
  },
  codeCopyText: { fontSize: Type.micro, fontWeight: '600', color: Editorial.ink },
  codeHint: { fontSize: Type.micro, color: Editorial.textCaption, marginTop: 8 },
  kakaoBtn: {
    marginTop: 24,
    height: 48,
    borderRadius: 12,
    backgroundColor: Editorial.kakao,
    alignItems: 'center',
    justifyContent: 'center',
  },
  kakaoBtnText: { fontSize: Type.footnote, fontWeight: '600', color: '#3c1e1e' },
  snsBtn: {
    marginTop: 10,
    height: 48,
    borderRadius: 12,
    backgroundColor: Editorial.surface,
    borderWidth: 1, borderColor: Editorial.line,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  snsBtnText: { fontSize: Type.footnote, fontWeight: '600', color: Editorial.textSoft },
  codeInput: {
    height: 48,
    borderRadius: 12,
    backgroundColor: Editorial.surfaceSoft,
    borderWidth: 1, borderColor: Editorial.line,
    paddingHorizontal: 16,
    fontSize: Type.body,
    color: Editorial.ink,
    letterSpacing: 2,
    marginBottom: 16,
  },
});
