# Legacy 7-target VLM archive

이 폴더의 CSV는 현재 API/DB/ML 계약에 쓰지 않는 과거 자료다.

처음 기준은 아래처럼 7개 신체치수를 중심으로 했다.

1. `chest`
2. `waist`
3. `hip`
4. `thigh` — 과거에는 허벅지둘레
5. `calf` — 과거에는 종아리둘레
6. `arm` — 과거에는 팔뚝둘레
7. `shoulder`

현재 기준은 11개 항목이다.

1. `shoulder`
2. `chest`
3. `waist`
4. `hip`
5. `thigh_length` — 샅선/인심 라인 → 무릎뼈
6. `calf_length` — 무릎뼈 → 복사뼈/발목
7. `torso_length` — 어깨선 → 골반점
8. `leg_length` — 샅선/인심 라인 → 복사뼈/발목
9. `neck_length` — 정면 기준 턱밑/턱끝 라인 → 목앞/쇄골 라인
10. `thigh_calf_ratio`
11. `torso_leg_ratio`

CSV 맨 위에 주석 행을 직접 넣으면 일반 CSV 파서가 깨질 수 있어서, 원본 형태는 유지하고 이 README에 전환 배경을 남긴다.
