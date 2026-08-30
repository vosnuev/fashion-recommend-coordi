import { Icon } from '@/components/icon';
import { useToast } from '@/components/ui';
import { router } from 'expo-router';
import { useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ContentMax, Editorial, ink, Type } from '@/constants/theme';
import { APP_VERSION, FAQ, OSS_LICENSES, SUPPORT_EMAIL } from '@/constants/support';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { goBack } from '@/lib/goBack';

const INK = Editorial.ink;

/**
 * 도움말·문의 — 자주 묻는 것 · 문의 · 오픈소스 고지 · 앱 정보.
 *
 * FAQ 를 먼저 두는 이유: 대부분의 문의는 "옷이 왜 아직 안 보이냐"처럼 이미 답이 있는 것이라,
 * 메일을 쓰기 전에 답을 만나는 편이 서로 빠르다.
 */
export default function SupportScreen() {
  const { contentStyle } = useBreakpoint();
  const toast = useToast();
  const [openFaq, setOpenFaq] = useState<number | null>(0);

  const mail = async () => {
    const url = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(`[cozy ${APP_VERSION}] 문의`)}`;
    const ok = await Linking.canOpenURL(url).catch(() => false);
    if (!ok) {
      // 메일 앱이 없는 기기·브라우저가 있다 — 주소라도 보여줘야 막다른 길이 안 된다.
      toast(`${SUPPORT_EMAIL} 로 보내주세요`);
      return;
    }
    Linking.openURL(url);
  };

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.headerSafe}>
        <View style={[styles.header, contentStyle(ContentMax.narrow)]}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/my')}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <Text style={styles.headerTitle}>도움말·문의</Text>
        </View>
      </SafeAreaView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: 24 },
          contentStyle(ContentMax.narrow),
        ]}>
        <Text style={styles.sectionTitle}>자주 묻는 것</Text>
        <View style={styles.card}>
          {FAQ.map((item, i) => {
            const open = openFaq === i;
            return (
              <View key={item.q}>
                <Pressable style={styles.faqHead} onPress={() => setOpenFaq(open ? null : i)}>
                  <Text style={styles.faqQ}>{item.q}</Text>
                  <Icon
                    name={open ? 'chevron.down' : 'chevron.right'}
                    tintColor={ink(0.3)}
                    size={15}
                  />
                </Pressable>
                {open ? <Text style={styles.faqA}>{item.a}</Text> : null}
                {i < FAQ.length - 1 ? <View style={styles.line} /> : null}
              </View>
            );
          })}
        </View>

        <Text style={styles.sectionTitle}>더 물어볼 것이 있다면</Text>
        <Pressable style={styles.mailRow} onPress={mail}>
          <View style={styles.mailIcon}>
            <Icon name="bubble.left" tintColor={INK} size={17} />
          </View>
          <View style={styles.mailBody}>
            <Text style={styles.mailTitle}>메일로 문의하기</Text>
            <Text style={styles.mailSub}>{SUPPORT_EMAIL}</Text>
          </View>
          <Icon name="chevron.right" tintColor={ink(0.25)} size={14} />
        </Pressable>

        <Text style={styles.sectionTitle}>약관·정책</Text>
        <View style={styles.card}>
          <Pressable
            style={styles.linkRow}
            onPress={() => router.push('/terms?doc=terms')}>
            <Text style={styles.linkLabel}>이용약관</Text>
            <Icon name="chevron.right" tintColor={ink(0.25)} size={14} />
          </Pressable>
          <View style={styles.line} />
          <Pressable
            style={styles.linkRow}
            onPress={() => router.push('/terms?doc=privacy')}>
            <Text style={styles.linkLabel}>개인정보 처리방침</Text>
            <Icon name="chevron.right" tintColor={ink(0.25)} size={14} />
          </Pressable>
        </View>

        <Text style={styles.sectionTitle}>오픈소스 고지</Text>
        <View style={styles.card}>
          {OSS_LICENSES.map((l, i) => (
            <View key={l.name}>
              <View style={styles.ossRow}>
                <Text style={styles.ossName}>{l.name}</Text>
                <Text style={styles.ossLicense}>{l.license}</Text>
              </View>
              {i < OSS_LICENSES.length - 1 ? <View style={styles.line} /> : null}
            </View>
          ))}
        </View>

        <Text style={styles.version}>cozy · {APP_VERSION}</Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  headerSafe: { backgroundColor: Editorial.page },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 10,
  },
  headerTitle: { fontSize: Type.label, fontWeight: '600', color: INK },

  content: { paddingHorizontal: 20, paddingTop: 8 },
  sectionTitle: {
    fontSize: Type.caption,
    fontWeight: '600',
    color: Editorial.textCaption,
    marginTop: 26,
    marginBottom: 10,
  },
  card: {
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 16,
    overflow: 'hidden',
  },
  line: { height: 1, backgroundColor: ink(0.07), marginHorizontal: 14 },

  faqHead: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 15,
  },
  faqQ: { flex: 1, fontSize: Type.footnote, fontWeight: '500', color: INK },
  faqA: {
    fontSize: Type.caption,
    color: Editorial.textSoft,
    lineHeight: 21,
    paddingHorizontal: 14,
    paddingBottom: 16,
    marginTop: -4,
  },

  mailRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 16,
  },
  mailIcon: {
    width: 34,
    height: 34,
    borderRadius: 17,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  mailBody: { flex: 1, gap: 3 },
  mailTitle: { fontSize: Type.footnote, fontWeight: '600', color: INK },
  mailSub: { fontSize: Type.micro, color: Editorial.textCaption },

  linkRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 15,
  },
  linkLabel: { flex: 1, fontSize: Type.footnote, color: INK },

  ossRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  ossName: { flex: 1, fontSize: Type.caption, color: Editorial.textSoft },
  ossLicense: { fontSize: Type.micro, color: Editorial.textMuted },

  version: {
    marginTop: 28,
    textAlign: 'center',
    fontSize: Type.micro,
    color: Editorial.textMuted,
  },
});
