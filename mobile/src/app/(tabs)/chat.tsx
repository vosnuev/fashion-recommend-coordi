import { StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ChatRoomView } from '@/components/chat/chat-room-view';
import { SessionList } from '@/components/chat/session-list';
import { Editorial } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';

/**
 * C1 채팅 탭.
 *
 * 넓은 화면에선 다른 화면과 같은 리듬으로 **대화 + 오른쪽 지난 대화 목록**을 보여준다
 * (목록만 덜렁 띄우면 오른쪽 열이 비어 다른 화면과 어긋난다).
 * 좁은 화면에선 대화를 담을 폭이 없으니 목록 화면으로 두고, 대화는 골라서 들어간다.
 */
export default function ChatScreen() {
  const { isWide } = useBreakpoint();

  if (isWide) return <ChatRoomView />;

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        <SessionList variant="screen" />
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
});
