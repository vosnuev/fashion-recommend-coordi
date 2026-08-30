# VLM 11개 항목 결과

새 `body_measurement_prompt_full.j2`와 `body_measurement_schema_full.json`으로 실행한 원본 응답·행별 예측·평가 결과를 이 디렉터리에 저장한다.

현재 11개 기준은 `torso_length`, `leg_length`를 저장 항목으로 포함한다. 비율은 VLM이 직접 반환하지 않고 서버/후처리에서 길이값으로 계산한다.

기존 `experiments/vlm` 또는 `redefined-9targets` 결과는 구형 둘레/팔뚝 또는 과거 비율 스키마가 섞일 수 있으므로 새 길이 정답과 섞지 않는다.
