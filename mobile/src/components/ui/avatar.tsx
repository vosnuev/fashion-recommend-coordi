import { StyleSheet, Text, View, type ViewStyle } from 'react-native';

import { Icon } from '@/components/icon';
import { SmartImage } from '@/components/ui/smart-image';
import { Editorial, Fonts, ink } from '@/constants/theme';

/**
 * 프로필 아바타 — 사진이 있으면 사진, 없으면 이름 첫 글자를 새긴 모노그램.
 *
 * 사진이 없으면 이름 첫 글자로, **이름도 없으면 사람 아이콘**으로 떨어진다.
 * 막 가입한 계정은 둘 다 없다 — 그 자리를 목업 사진이나 서비스 이름으로 채우면
 * 처음 들어온 사람이 남의 얼굴·남의 이름을 자기 프로필로 보게 된다.
 * 면은 순백이라 원은 테두리로만 그린다.
 */
export function Avatar({
  name,
  uri,
  asset,
  size = 52,
  style,
}: {
  name?: string | null;
  uri?: string | null;
  /** 번들에 포함된 이미지 — require(...) 결과 */
  asset?: number;
  size?: number;
  style?: ViewStyle;
}) {
  const circle: ViewStyle = { width: size, height: size, borderRadius: size / 2 };

  if (asset || uri) {
    return (
      <SmartImage
        asset={asset}
        uri={uri}
        width={size}
        height={size}
        radius={size / 2}
        style={{ ...styles.photo, ...style }}
      />
    );
  }

  const initial = name?.trim().slice(0, 1).toUpperCase();
  return (
    <View style={[styles.circle, circle, style]}>
      {initial ? (
        <Text style={[styles.initial, { fontSize: size * 0.42 }]}>{initial}</Text>
      ) : (
        <Icon name="person" tintColor={ink(0.35)} size={size * 0.46} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  circle: {
    borderWidth: 1,
    borderColor: Editorial.lineStrong,
    backgroundColor: Editorial.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  /* 사진 가장자리가 흰 배경에 묻히지 않게 얇은 테두리를 두른다 */
  photo: { borderWidth: 1, borderColor: Editorial.line },
  initial: { fontFamily: Fonts.serif, color: Editorial.ink, includeFontPadding: false },
});
