import Ionicons from '@expo/vector-icons/Ionicons';
import { StyleProp, TextStyle } from 'react-native';

/**
 * 크로스플랫폼 아이콘.
 * 기존에 쓰던 SF Symbol 이름을 Ionicons로 매핑해, iOS·Android·웹 모두에서 렌더된다.
 * (expo-symbols의 SymbolView는 iOS 전용이라 웹/안드로이드에서 빈칸으로 나옴 → 이걸로 대체)
 */
const MAP = {
  'chevron.left': 'chevron-back',
  'chevron.right': 'chevron-forward',
  'chevron.down': 'chevron-down',
  'chevron.up': 'chevron-up',
  'trash': 'trash-outline',
  'square.and.pencil': 'create-outline',
  'pencil': 'pencil',
  'sparkles': 'sparkles',
  'plus': 'add',
  'magnifyingglass': 'search',
  'slider.horizontal.3': 'options-outline',
  'arrow.right': 'arrow-forward',
  'arrow.up': 'arrow-up',
  'arrow.clockwise': 'refresh',
  'lock.shield': 'shield-checkmark-outline',
  'lock': 'lock-closed-outline',
  'heart': 'heart-outline',
  'heart.fill': 'heart',
  'star': 'star-outline',
  'star.fill': 'star',
  'hand.thumbsup': 'thumbs-up-outline',
  'hand.thumbsdown': 'thumbs-down-outline',
  'figure.stand': 'body-outline',
  'exclamationmark.triangle': 'warning-outline',
  'ellipsis': 'ellipsis-horizontal',
  'line.3.horizontal': 'menu',
  'checkmark': 'checkmark',
  'xmark': 'close',
  'checkmark.circle.fill': 'checkmark-circle',
  'camera': 'camera-outline',
  'calendar': 'calendar-outline',
  'bubble.left.and.bubble.right': 'chatbubbles-outline',
  'bubble.left': 'chatbubble-outline',
  'house': 'home-outline',
  'book': 'book-outline',
  'person': 'person-outline',
  'person.2': 'people-outline',
  'link': 'link-outline',
  'square.and.arrow.up': 'share-outline',
  'bookmark': 'bookmark-outline',
  'archivebox': 'archive-outline',
  'bookmark.fill': 'bookmark',
  'arrow.up.right.square': 'open-outline',
  'bell': 'notifications-outline',
  'location': 'location-outline',
  'photo': 'image-outline',
  'photo.on.rectangle': 'images-outline',
  'globe': 'globe-outline',
  'building.columns': 'library-outline',
  'questionmark.circle': 'help-circle-outline',
  'ruler': 'resize-outline',
  'sun.max': 'sunny-outline',
  'tshirt': 'shirt-outline',
  'paintpalette': 'color-palette-outline',
  'wallet': 'wallet-outline',
} satisfies Record<string, keyof typeof Ionicons.glyphMap>;

export type IconName = keyof typeof MAP;

export function Icon({
  name,
  tintColor,
  size = 20,
  style,
}: {
  name: IconName;
  tintColor?: string;
  size?: number;
  style?: StyleProp<TextStyle>;
}) {
  return <Ionicons name={MAP[name]} size={size} color={tintColor} style={style} />;
}
