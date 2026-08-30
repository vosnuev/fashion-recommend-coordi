# 설계 기록

- 백엔드: Django REST Framework
- 모바일: Expo/React Native
- 데이터: PostgreSQL, Redis, Qdrant
- 공유 기능: `wardrobe` 앱의 공유 옷장 도메인과 모바일 공유 공간 UI를 연결한다.
- 추천 기능: `recommend` 및 `chat` 파이프라인 결과를 모바일 추천 API/UI에 연결한다.
- 시크릿: Infisical 실행 시점 주입을 사용하고 원문을 코드·로그에 남기지 않는다.
