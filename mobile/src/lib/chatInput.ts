export type ChatInputKeyPress = {
  key: string;
  shiftKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
};

/** 웹 채팅에서 줄바꿈이 아닌 Enter만 전송으로 해석한다. */
export function shouldSubmitChatInputOnKeyPress(
  platform: string,
  event: ChatInputKeyPress,
): boolean {
  return (
    platform === 'web' &&
    event.key === 'Enter' &&
    !event.shiftKey &&
    !event.isComposing &&
    event.keyCode !== 229
  );
}
