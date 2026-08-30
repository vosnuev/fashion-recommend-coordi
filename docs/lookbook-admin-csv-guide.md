# 운영자 룩북 CSV 등록 가이드

## 1. 등록 파일 위치

VS Code에서 아래 두 CSV를 함께 편집한다.

```text
SKN28-FINAL-1Team/
└─ data/
   └─ lookbook/
      ├─ admin_looks.csv
      ├─ admin_look_items.csv
      └─ images/
```

- `admin_looks.csv`: 룩북 카드와 전신사진을 한 줄에 하나씩 등록한다.
- `admin_look_items.csv`: 각 룩의 상의·하의·신발·액세서리와 구매 링크를 등록한다.
- `images/`: 룩북 카드 및 상세 화면에 표시할 전신사진을 넣는다.

두 CSV는 한 세트다. `admin_looks.csv`의 `external_id`와 `admin_look_items.csv`의 `look_external_id`가 정확히 같아야 연결된다.

> `data/`는 상품 이미지와 운영 데이터를 Git에 잘못 올리지 않도록 `.gitignore` 대상이다. 로컬에서 수정한 CSV는 자동으로 커밋되지 않는다. 팀 공유가 필요하면 저작권과 개인정보를 확인한 뒤 승인된 별도 저장소 또는 배포 스토리지를 사용한다.

---

## 2. VS Code에서 여는 방법

### 방법 A: VS Code 탐색기에서 열기

1. VS Code에서 `SKN28-FINAL-1Team` 프로젝트를 연다.
2. 왼쪽 `Explorer` 아이콘을 누른다.
3. `data` → `lookbook`을 차례대로 펼친다.
4. 먼저 `admin_looks.csv`를 클릭한다.
5. 새 탭에서 `admin_look_items.csv`도 연다.
6. 전신사진은 `data/lookbook/images` 폴더로 드래그한다.

`data` 폴더가 보이지 않으면 VS Code 탐색기의 새로고침 버튼을 누르고, 설정에서 제외된 파일 표시 여부를 확인한다.

### 방법 B: CMD에서 바로 열기

프로젝트 루트에서 다음 명령을 실행한다.

```cmd
code data\lookbook\admin_looks.csv
code data\lookbook\admin_look_items.csv
```

두 파일을 동시에 열려면 다음과 같이 실행한다.

```cmd
code data\lookbook\admin_looks.csv data\lookbook\admin_look_items.csv
```

`code` 명령을 찾을 수 없다는 메시지가 나오면 VS Code에서 직접 연다. 또는 VS Code 명령 팔레트에서 `Shell Command: Install 'code' command in PATH`를 실행한 뒤 터미널을 다시 연다.

### CSV 편집 확장 프로그램

기본 텍스트 편집기로도 작업할 수 있지만, 열을 표처럼 보려면 VS Code의 `Rainbow CSV` 같은 CSV 확장 프로그램을 사용할 수 있다. 확장 프로그램 설치가 필수는 아니다.

---

## 3. 권장 등록 수량과 ID 규칙

현재 룩북 카테고리는 다음 여덟 가지를 기준으로 관리한다.

| 카테고리 | WOMAN 목표 | MAN 목표 |
|---|---:|---:|
| 출근 | 50 | 50 |
| 데이트 | 50 | 50 |
| 나들이 | 50 | 50 |
| 여행 | 50 | 50 |
| 미니멀 | 50 | 50 |
| 캐주얼 | 50 | 50 |
| 빈티지 | 50 | 50 |
| 스트릿 | 50 | 50 |
| 하객룩 | 50 | 해당 없음 |

- 여성 룩 합계: 450개
- 남성 룩 합계: 400개
- 전체 목표: 850개

`external_id`는 전체 데이터에서 절대 중복되면 안 된다. 성별과 카테고리를 포함한 다음 형식을 권장한다.

```text
woman-work-001       ~ woman-work-050
woman-date-001       ~ woman-date-050
woman-outing-001     ~ woman-outing-050
woman-travel-001     ~ woman-travel-050
woman-minimal-001    ~ woman-minimal-050
woman-casual-001     ~ woman-casual-050
woman-vintage-001    ~ woman-vintage-050
woman-street-001     ~ woman-street-050
woman-wedding-guest-001 ~ woman-wedding-guest-050

man-work-001         ~ man-work-050
man-date-001         ~ man-date-050
man-outing-001       ~ man-outing-050
man-travel-001       ~ man-travel-050
man-minimal-001      ~ man-minimal-050
man-casual-001       ~ man-casual-050
man-vintage-001      ~ man-vintage-050
man-street-001       ~ man-street-050
```

숫자는 항상 세 자리로 작성한다. 기존 ID를 바꾸면 새 룩으로 등록되므로 한번 정한 ID는 유지한다.

---

## 4. `admin_looks.csv` 작성법

헤더는 삭제하거나 순서를 임의로 바꾸지 않는다.

```csv
external_id,gender,category,title,subtitle,cover_image,tags,is_active
street-vintage-001,WOMAN,캐주얼,마이애미 빈티지 스트릿 룩,버건디 · 빈티지 · 스트릿,images/street-vintage-001.png,캐주얼|나들이|빈티지|스트릿,true
```

위 행은 최초 테스트에 사용한 실제 예시다. 전신사진도 아래 위치에 이미 준비되어 있다.

```text
data/lookbook/images/street-vintage-001.png
```

| 컬럼 | 필수 | 작성 규칙 |
|---|---|---|
| `external_id` | 필수 | 전체에서 유일한 영문·숫자·하이픈 ID |
| `gender` | 필수 | 여성은 `WOMAN`, 남성은 `MAN`만 허용 |
| `category` | 필수 | `출근`, `데이트`, `나들이`, `여행`, `미니멀`, `캐주얼`, `빈티지`, `스트릿`, WOMAN의 경우 `하객룩` 중 하나 |
| `title` | 필수 | 사용자에게 표시할 룩 이름 |
| `subtitle` | 선택 | 색상·스타일·상황을 ` · `로 구분 |
| `cover_image` | 필수 | `images/파일명.png` 형태의 상대 경로 |
| `tags` | 선택 | 여러 태그를 쉼표가 아닌 `|`로 구분 |
| `is_active` | 필수 | 노출은 `true`, 숨김은 `false` |

### 전신사진 파일 규칙

- 권장 파일명: `external_id`와 동일한 이름
- 예: `woman-casual-001.png`
- CSV 경로: `images/woman-casual-001.png`
- 권장 형태: 세로 전신사진, 동일한 비율과 해상도
- 파일명에는 공백·한글·괄호를 사용하지 않는다.
- 상용화 시 직접 촬영, 정식 라이선스 또는 권리자의 이용허락이 확인된 이미지만 사용한다.

---

## 5. `admin_look_items.csv` 작성법

룩 하나에 보통 3~4줄을 작성한다. 상의·하의·신발은 기본이며 액세서리가 있으면 한 줄을 추가한다.

```csv
look_external_id,slot,category_small,name,brand,price,product_url,image_url,related_keyword,sort_order
street-vintage-001,상의,티셔츠,[당일출고]miami 빈티지 프린팅 셔링 반팔티 4color,이너니티,28000,https://shopping.naver.com/window-products/style/13623253921,https://shop-phinf.pstatic.net/20260605_110/1780622711630YoYFX_JPEG/66518542615495680_1437778376.jpeg?type=m450,버건디 빈티지 프린팅 슬림 반팔티,0
street-vintage-001,하의,데님 팬츠,[당일출고] 빈티지 캣워싱 구제 버뮤다 데님팬츠 3color,이너니티,38400,https://shopping.naver.com/window-products/style/12054419153,https://shop-phinf.pstatic.net/20260602_140/1780387751097TObMB_JPEG/45982200158121192_928421875.jpg?type=m450,블랙 구제 워싱 버뮤다 데님팬츠,1
street-vintage-001,신발,부츠,당일출고 [5cm] 블레이드 버클 워커 미들부츠,이너니티,38000,https://shopping.naver.com/window-products/style/13523565054,https://shop-phinf.pstatic.net/20260513_59/1778664849693vdoIN_JPEG/7782086749186374_1675422145.jpeg?type=m450,블랙 버클 워커 미들부츠,2
```

이 세 행은 사용자가 최초 테스트용으로 제공한 상의·하의·신발 네이버 쇼핑 링크를 연결한 완성 예시다. 세 행의 `look_external_id`가 모두 `street-vintage-001`이기 때문에 위의 룩 한 개 아래에 구성 상품으로 묶인다.

| 컬럼 | 필수 | 작성 규칙 |
|---|---|---|
| `look_external_id` | 필수 | `admin_looks.csv`의 `external_id`와 완전히 동일 |
| `slot` | 필수 | `상의`, `하의`, `신발`, `액세서리` 중 하나 |
| `category_small` | 필수 | 관리자가 실물을 검수해 아래 공식 소분류 중 하나를 정확히 입력 |
| `name` | 필수 | 실제 상품명. 쉼표가 있으면 전체 값을 큰따옴표로 감쌈 |
| `brand` | 선택 | 브랜드 또는 판매처 이름 |
| `price` | 선택 | 쉼표와 `원` 없이 숫자만 입력. 예: `28000` |
| `product_url` | 필수 | 사용자가 이동할 정상적인 공식 상품 상세 URL |
| `image_url` | 선택 | 약관·이용허락 범위에서 표시 가능한 대표 이미지 URL |
| `related_keyword` | 필수 | 비슷한 상품 검색용 핵심 키워드 4~7개 |
| `sort_order` | 필수 | 상의 `0`, 하의 `1`, 신발 `2`, 액세서리 `3` 권장 |

한 룩 안에서 동일한 `slot`을 두 번 사용할 수 없다. 상품이 두 개인 경우 `액세서리`를 임의로 반복하지 말고, 데이터 구조 확장 여부를 개발자와 먼저 확인한다.

### 관리자 검수용 대분류·소분류 표

`slot`은 시스템의 대분류이며 `category_small`은 관리자가 상품 사진과 상세페이지를 보고 확정하는 소분류다. 표에 없는 표현이나 대분류와 맞지 않는 조합은 CSV 가져오기 단계에서 거부된다.

| `slot`(대분류) | 허용되는 `category_small`(소분류) |
|---|---|
| 상의 | 티셔츠, 셔츠/블라우스, 니트/스웨터, 후드/맨투맨, 민소매 |
| 하의 | 데님 팬츠, 슬랙스, 코튼 팬츠, 트레이닝 팬츠, 숏팬츠, 스커트, 레깅스 |
| 아우터 | 자켓, 코트, 패딩, 점퍼/블루종, 가디건, 후드집업, 베스트 |
| 원피스/세트 | 원피스, 점프수트/오버롤, 셋업, 파자마/홈웨어 세트 |
| 신발 | 스니커즈, 구두/로퍼, 부츠, 샌들/슬리퍼, 플랫/단화 |
| 가방 | 백팩, 크로스백, 숄더백, 토트백, 에코백, 클러치/파우치, 지갑 |
| 액세서리 | 모자, 벨트, 주얼리, 머플러/스카프, 양말, 안경/선글라스, 헤어 액세서리 |
| 언더웨어/이너웨어 | 브라, 팬티/드로즈, 런닝/캐미솔, 속바지, 보정속옷, 내복/발열 이너 |

예를 들어 원본이 가디건이라면 `아우터,가디건`으로 입력한다. `아우터,패딩` 후보는 비슷한 상품에서 제외된다. 기존 DB 행은 마이그레이션 후 소분류가 공란이므로, 관리자가 검수해 CSV로 다시 등록하기 전까지 해당 행의 비슷한 상품은 노출되지 않는다.

### 연관 검색어 작성 요령

상품명 전체를 그대로 복사하기보다 다음 순서로 핵심 특징을 적는다.

```text
색상 + 소재/패턴 + 핏 + 품목
```

좋은 예:

```text
버건디 빈티지 프린팅 슬림 반팔티
블랙 구제 워싱 버뮤다 데님팬츠
블랙 버클 워커 미들부츠
```

피해야 할 예:

```text
예쁜 옷
신상 추천
[당일출고 무료배송 쿠폰] 상품명 전체
```

---

## 6. CSV에서 특히 주의할 점

1. CSV 구분자는 쉼표다. 상품명에 쉼표가 들어가면 `"상품명, 옵션 포함"`처럼 큰따옴표로 감싼다.
2. 가격에 `38,400`을 입력하면 열이 두 개로 갈라진다. 반드시 `38400`으로 입력한다.
3. 태그만 `|`로 구분한다. 예: `캐주얼|나들이|빈티지`.
4. URL 앞뒤에 공백을 넣지 않는다.
5. Excel에서 저장할 경우 `CSV UTF-8(쉼표로 분리)` 형식을 선택한다.
6. `external_id`와 `look_external_id`의 대소문자와 하이픈까지 같아야 한다.
7. WOMAN과 MAN 데이터에 같은 `external_id`를 재사용하지 않는다.
8. CSV의 첫 번째 헤더 줄은 삭제하지 않는다.
9. 기존 룩을 숨기려면 행을 지우기보다 `is_active`를 `false`로 바꾼다.
10. 작업 전 CSV를 복사해 날짜가 포함된 로컬 백업을 만들어 둔다.

---

## 7. 등록 전 검증

프로젝트 루트의 CMD에서 다음 명령을 실행한다. 이 단계는 DB에 저장하지 않고 CSV 형식과 연결 관계만 확인한다.

```cmd
docker compose exec api python manage.py import_admin_lookbook /data/lookbook --dry-run
```

모든 성별·카테고리를 정확히 50개씩 채운 최종 검수에서는 다음 명령을 사용한다.

```cmd
docker compose exec api python manage.py import_admin_lookbook /data/lookbook --dry-run --require-50
```

`검증 완료`가 표시돼야 실제 반영 단계로 넘어간다. 오류가 나오면 메시지에 표시된 누락 컬럼, 중복 ID 또는 존재하지 않는 룩 ID를 먼저 수정한다.

> 개발 중 일부 카테고리만 작성했다면 `--require-50`을 빼고 검증한다. 이 옵션은 모든 카테고리 입력이 끝난 최종 검수용이다.

---

## 8. DB에 실제 반영

먼저 성별 컬럼 마이그레이션을 적용한다.

```cmd
docker compose exec api python manage.py migrate
```

CSV 검증이 성공한 다음 실제 데이터를 반영한다.

```cmd
docker compose exec api python manage.py import_admin_lookbook /data/lookbook
```

같은 `external_id`로 다시 실행하면 해당 룩이 갱신되므로 가격, 제목, 태그 또는 링크를 수정한 뒤 재실행할 수 있다.

---

## 9. API 확인

여성 룩 한 개 확인:

```cmd
curl "http://localhost:8000/api/v1/lookbooks/discover/?gender=WOMAN&limit=1"
```

남성 룩 한 개 확인:

```cmd
curl "http://localhost:8000/api/v1/lookbooks/discover/?gender=MAN&limit=1"
```

`results`에 등록한 `title`, `gender`, `image`, `items`가 표시되는지 확인한다.

---

## 10. 화면 확인

1. 백엔드와 모바일 웹을 실행한다.
2. 룩북의 `둘러보기` 화면으로 이동한다.
3. 기본값 `WOMAN`에서 여성 CSV 데이터만 보이는지 확인한다.
4. 오른쪽 토글을 한 번 누른다.
5. 표시가 `MAN`으로 바뀌고 흰색 원형 손잡이가 반대 방향으로 이동하는지 확인한다.
6. 남성 CSV 데이터만 보이는지 확인한다.
7. 다시 누르면 `WOMAN`으로 돌아오는지 확인한다.
8. 룩 카드를 누르고 구성 상품과 구매 링크가 올바른지 확인한다.

토글은 COZY의 기존 `Editorial.selected` 색상과 UI 시스템 폰트를 사용하며, 텍스트는 검은색 계열로 표시한다.

---

## 11. 권장 작업 순서

한 번에 600개를 입력하지 말고 다음 단위로 진행한다.

1. WOMAN 한 카테고리에서 3개만 시험 등록
2. `--dry-run` 검증
3. DB 반영 후 목록과 상세 화면 확인
4. WOMAN 해당 카테고리를 50개까지 확장
5. 나머지 WOMAN 카테고리를 같은 방식으로 등록
6. MAN 한 카테고리에서 3개 시험 등록
7. 토글과 상세 화면 확인
8. MAN 각 카테고리를 50개까지 확장
9. `--dry-run --require-50`으로 최종 수량 검증

이 순서를 따르면 ID 또는 컬럼 작성 방식이 잘못됐을 때 수백 개 행을 다시 수정하는 일을 줄일 수 있다.
