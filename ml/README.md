# ML

## 1. 기능별 구성

```text
ml/
├── body_measurement/     # 신체치수 추정: 학습, VLM 평가, API 추론 코드
│   ├── src/              # 재사용 가능한 모델·평가 로직
│   ├── scripts/          # 직접 실행하는 CLI
│   ├── prompts/          # VLM 프롬프트와 응답 schema
│   ├── experiments/      # 모델별·실행별 테스트 결과
│   ├── artifacts/models/ # API가 읽는 서빙 모델
│   └── reports/          # 사람이 읽는 확정 보고서
```

## 2. 새 ML 기능 추가 기준

새 기능은 `ml/<feature_name>/` 아래에 독립적으로 만든다. 데이터와 모델 가중치는 Git에
커밋하지 않고 S3 경로와 버전을 기록한다. API가 호출하는 추론 진입점은 해당 기능의
`src/inference.py`에 둔다.
