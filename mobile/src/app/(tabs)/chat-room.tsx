import { useLocalSearchParams } from 'expo-router';

import { ChatRoomView } from '@/components/chat/chat-room-view';

/**
 * C2 채팅 대화 화면.
 * 본문은 ChatRoomView 가 담당한다 — /chat 도 같은 것을 그리므로 컴포넌트를 공유한다.
 *
 * `from` 은 들어온 자리다. 채팅방은 목록·저장 룩·코지 패널 등 여러 곳에서 열리는데,
 * 돌아갈 자리를 하나로 박아 두면 어느 길로 들어왔든 같은 곳으로 튕긴다
 * (룩 상세·저장 룩이 쓰는 것과 같은 패턴 — lib/goBack.ts 의 withReturn/backTo).
 */
export default function ChatRoom() {
  const { id, from } = useLocalSearchParams<{ id?: string; from?: string }>();
  // 다른 화면에서 밀고 들어온 경우가 있어 뒤로가기를 둔다.
  return <ChatRoomView sessionId={id} from={from} showBack />;
}
