export type MeasurementResultSource = {
  usedPhotos: boolean;
  photoFallback: boolean;
};

export type PhotoQualityFailureState = {
  photoQualityFailed: boolean;
};

export function isPhotoQualityFailureCode(errorCode: string | null | undefined): boolean {
  return errorCode === 'photo_quality_failed';
}

export function photoMeasurementFailureState(
  errorCode: string | null | undefined,
): PhotoQualityFailureState {
  return { photoQualityFailed: isPhotoQualityFailureCode(errorCode) };
}

export function measurementResultSource(
  usedPhotos: boolean,
  photoFallback = false,
): MeasurementResultSource {
  return { usedPhotos, photoFallback };
}

export function measurementRequestFailureState(): PhotoQualityFailureState {
  return { photoQualityFailed: false };
}

export function measurementResultDescription(source: MeasurementResultSource): string {
  if (source.usedPhotos) return '사진과 입력 정보로 추정한 결과예요.';
  if (source.photoFallback) {
    return '사진을 인식하지 못해 키·몸무게·성별만으로 추정한 값입니다.';
  }
  return '키·몸무게·성별로 추정한 결과예요.';
}
