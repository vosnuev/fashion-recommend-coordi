# Legacy body measurement experiments

이 폴더는 현재 API/DB 계약에서 쓰지 않는 과거 실험 코드를 보관한다.

- 과거 기준: `thigh`, `calf`, `arm`을 둘레로 다루던 7개/10개 실험
- 현재 기준: `shoulder`, `chest`, `waist`, `hip`, `thigh_length`, `calf_length`, `torso_length`, `leg_length`, `neck_length`, `thigh_calf_ratio`, `torso_leg_ratio`

Swagger/API 확인과 운영 추론은 상위 `src/inference.py`와 `scripts/retrain_11targets.py` 기준으로 본다.
