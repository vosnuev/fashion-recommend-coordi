import * as ImagePicker from 'expo-image-picker';
import { Platform } from 'react-native';

async function ensurePermission(kind: 'library' | 'camera'): Promise<boolean> {
  const request =
    kind === 'library'
      ? ImagePicker.requestMediaLibraryPermissionsAsync
      : ImagePicker.requestCameraPermissionsAsync;
  const { status } = await request();
  return status === 'granted';
}

/** 앨범에서 옷 사진 1장 선택 */
export async function pickFromAlbum(): Promise<string | null> {
  if (!(await ensurePermission('library'))) return null;
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images'],
    allowsEditing: true,
    aspect: [4, 5],
    quality: 0.9,
  });
  if (result.canceled || !result.assets[0]) return null;
  return result.assets[0].uri;
}

/** 체형측정용 전신 사진 1장 선택 (앨범). 크롭 없이 원본 비율 유지 — 전신이 잘리면 안 되므로. */
export async function pickBodyPhoto(): Promise<string | null> {
  if (!(await ensurePermission('library'))) return null;
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images'],
    quality: 0.8,
    /* iOS 26 기본(automatic=시트)에서 피커가 선택 후 확정도 닫기도 안 되는 상태로 굳는다.
       풀스크린으로 띄우면 정상적인 내비게이션 바(취소/추가)가 나온다. */
    presentationStyle: ImagePicker.UIImagePickerPresentationStyle.FULL_SCREEN,
  });
  if (result.canceled || !result.assets[0]) return null;
  return result.assets[0].uri;
}

/** 착장 분석용 전신 사진 1장 선택. 전신 비율을 보존해야 해 크롭을 강제하지 않는다. */
export async function pickOutfitPhoto(): Promise<string | null> {
  if (!(await ensurePermission('library'))) return null;
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: Platform.OS === 'ios' ? ['images', 'livePhotos'] : ['images'],
    quality: 0.9,
    /* 인물사진 HEIC와 Live Photo는 영상이 아니라 현재 대표 정지 프레임만 받는다.
       Compatible은 iOS가 업로드 가능한 표현(JPEG 등)을 우선 반환하게 한다. */
    preferredAssetRepresentationMode:
      ImagePicker.UIImagePickerPreferredAssetRepresentationMode.Compatible,
    presentationStyle: ImagePicker.UIImagePickerPresentationStyle.FULL_SCREEN,
  });
  if (result.canceled || !result.assets[0]) return null;
  return result.assets[0].uri;
}

/**
 * 채팅에 붙일 무드 참고 사진 1장 선택.
 * 크롭을 강제하지 않는다 — 분위기를 읽는 용도라 4:5 로 잘라내면 옷·배경이 함께 잘린다.
 */
export async function pickChatPhoto(): Promise<string | null> {
  return pickBodyPhoto();
}

/** 착장 분석용 촬영. 앨범 선택과 마찬가지로 원본 비율을 유지한다. */
export async function takeOutfitPhoto(): Promise<string | null> {
  if (!(await ensurePermission('camera'))) return null;
  const result = await ImagePicker.launchCameraAsync({ quality: 0.8 });
  if (result.canceled || !result.assets[0]) return null;
  return result.assets[0].uri;
}

/** 카메라로 옷 사진 촬영 */
export async function pickFromCamera(): Promise<string | null> {
  if (!(await ensurePermission('camera'))) return null;
  const result = await ImagePicker.launchCameraAsync({
    allowsEditing: true,
    aspect: [4, 5],
    quality: 0.9,
  });
  if (result.canceled || !result.assets[0]) return null;
  return result.assets[0].uri;
}

/**
 * 프로필 사진 1장 선택. 원형 아바타로 잘려 나가므로 1:1 크롭을 강제한다 —
 * 자유 비율로 두면 사용자가 맞춰 둔 구도가 원 안에서 잘려 얼굴이 치우친다.
 */
export async function pickProfilePhoto(): Promise<string | null> {
  if (!(await ensurePermission('library'))) return null;
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images'],
    allowsEditing: true,
    aspect: [1, 1],
    quality: 0.9,
  });
  if (result.canceled || !result.assets[0]) return null;
  return result.assets[0].uri;
}
