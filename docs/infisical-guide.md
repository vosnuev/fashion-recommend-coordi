# Infisical 온보딩 가이드

## 1. Infisical이란?

**Infisical**은 프로젝트 시크릿(API 키, DB 비밀번호 등)을 한곳에서 관리하는 중앙 저장소

- 시크릿을 웹 대시보드([app.infisical.com](https://app.infisical.com))에 저장합니다.
- 로컬에서는 CLI가 실행 시점에 환경변수로 주입합니다.
- `.env` 파일을 팀원끼리 주고받을 필요가 없습니다.
- 키가 바뀌면 대시보드에서 한 번만 수정하면 됩니다.

```text
[기존]  .env 파일 -> 전달 -> 각자 복사 -> 버전 어긋남

[이제]  Infisical 대시보드 (중앙 저장)
              |
              v
        infisical run --env=dev -- <명령>
```

### Infisical CLI 주입 과정

예를 들어 Windows PowerShell에서 다음을 실행한다고 하겠습니다.

```powershell
infisical run --env=dev --path=/ -- npm run dev
```

내부적으로는 다음 순서로 동작합니다.

1. Infisical CLI가 로그인 정보 또는 Machine Identity 토큰을 확인
2. Infisical 서버에서 dev, / 경로의 Secret을 가져옴
3. 가져온 값을 현재 Windows의 시스템 환경변수에 영구 등록하지 않고, 자식 프로세스용 환경변수 목록에 합침
4. Infisical CLI가 `npm run dev` 프로세스 실행
5. 애플리케이션은 일반 환경변수처럼 값을 읽습니다.

`process.env.DATABASE_URL`
`process.env.API_KEY`

6. `npm run dev`가 종료되면 주입된 환경변수도 함께 사라집니다.

즉, 다음 파일들은 변경되지 않습니다.

- Windows 시스템 환경변수
- PowerShell의 원래 환경변수
- .env
- 소스 코드

구조는 다음과 같습니다.

```text
  Infisical 서버
      ↓ Secret 조회
  Infisical CLI
      ↓ 프로세스 환경변수 생성
  npm / Python / Docker Compose
      ↓ 상속
  애플리케이션
```

---

## 2. 설치방법

### 1. 웹 로그인 확인

[https://app.infisical.com](https://app.infisical.com)에 접속해서 로그인이 되는지, `skn28-final-1team` 프로젝트가 보이는지 확인합니다.

### 2. CLI 설치

macOS(Homebrew):

```shell
brew install infisical/get-cli/infisical
```

Windows(NPM):

```shell
npm install -g @infisical/cli
```

설치 확인:

```shell
infisical --version
```

버전 번호가 출력되면 성공

### 3. 로그인

터미널에서 아래 명령을 실행

```shell
infisical login
```

- 브라우저가 자동으로 열리면 로그인
- 리전 선택이 나오면 반드시 **US (Infisical Cloud, app.infisical.com)** 를 선택

### 4. 프로젝트 연결

프로젝트 루트 디렉토리에서 아래 명령을 실행

```shell
cd <프로젝트 루트>
infisical init
```

실행하면 로그인된 계정의 조직과 프로젝트 목록이 표시됩니다.
`skn28-final-1team` 프로젝트를 선택하면 `.infisical.json` 파일이 생성됩니다.

`workspaceId` 출처: SKN28-FINAL-1TEAM(Organization) -> Secrets Management -> SKN28-FINAL-1TEAM(Project) -> Settings → General → Project Overview → Copy Project ID(오른쪽 버튼)

```json
{
    "workspaceId": "a9752e10-915a-410e-a714-e89f540ce5e8",
    "defaultEnvironment": "dev",
    "gitBranchToEnvironmentMapping": null
}
```

이 파일은 어떤 Infisical 프로젝트에 연결할지 CLI가 자동으로 인식하는 데 사용됩니다.

### 5. 환경변수 주입해서 실행하기

- CLI로 앱을 실행시키면, Infisical이 `dev` 환경에서 Secret key를 가져옵니다.
- Secret key가 환경변수로 바뀌어 앱에 들어갑니다.
- 앱은 평소처럼 `process.env.DB_PASSWORD`처럼 사용합니다.

핵심 패턴은 하나입니다.

- Infisical 값이 `.env` 값을 덮어씁니다 (이유: Infisical이 나중에 주입되므로).

```shell
infisical run --env=<환경> -- <실행할 명령>
# 예시
infisical run --env=dev -- uv run python main.py
infisical run --env=dev -- npm run dev
infisical run --env=dev -- python manage.py runserver
```

예외: **Docker Compose는 이 패턴만으로는 컨테이너에 시크릿이 전달되지 않습니다.**
우리 compose 파일은 컨테이너가 `.env` 파일에서 값을 읽는 구조이기 때문입니다.
반드시 아래 [3. Docker Compose에서 사용하기](#3-docker-compose에서-사용하기-중요) 절차를 따릅니다.

> 보안 주의: `infisical run` 아래에서 `env`, `printenv`처럼 환경변수 전체를 출력하는 명령을 실행하지 않습니다. 시크릿 값을 터미널, 채팅, 로그, 커밋에 남기지 않습니다.

### 6. 정리: 처음 오는 사람의 전체 흐름

1. Infisical 웹 로그인 확인 (**US 리전**, `skn28-final-1team` 프로젝트가 보이는지)
2. CLI 설치 (`brew install infisical/get-cli/infisical` 또는 `npm install -g @infisical/cli`)
3. `infisical login` (최초 1회, **US 리전** 선택)
4. 레포 clone 후 프로젝트 루트에서 `infisical init` 실행 → 조직/프로젝트 선택 시 `.infisical.json` 생성
5. **Docker로 실행할 경우**: 아래 [3장](#3-docker-compose에서-사용하기-중요)의 `./run.sh`(macOS/Linux) 또는 `./run.ps1`(Windows PowerShell) 실행 한 번이면 끝입니다.
6. **Docker 없이 로컬로 직접 실행할 경우**: `infisical run --env=dev -- <명령>` 패턴을 사용합니다.

---

## 3. Docker Compose에서 사용하기 (중요)

### 실행 방법

**가장 간단한 방법: 스크립트 실행**

```shell
# macOS/Linux
./run.sh

# Windows
./run.ps1
```

스크립트가 아래 두 단계를 자동으로 처리합니다.

```shell
# 1단계: Infisical에서 시크릿을 .env 파일로 내보내기
infisical export --env=dev --output-file=./.env

# 2단계: Docker Compose 실행 (.env 파일을 읽어 컨테이너에 주입)
docker compose --profile all up -d --build
```

**특정 프로필만 실행할 때** (스크립트 없이 직접):

```shell
infisical export --env=dev --output-file=./.env
docker compose --profile api up -d
```

**Infisical에서 값을 바꿨을 때** (컨테이너 재시작):

```shell
infisical export --env=dev --output-file=./.env
docker compose --profile <프로필> up -d --force-recreate
```

규칙:

- `.env`의 유일한 작성자는 Infisical입니다. 손으로 편집하지 않습니다.
- 이렇게 만든 `.env`는 로컬 캐시이며 `.gitignore`에 의해 커밋되지 않습니다.

### 알아두면 좋은 것

- `run.sh`/`run.ps1`은 `docker-compose.yml` + `docker-compose.swagger.yml` 두 파일을 함께 사용하고, `--profile all`로 db + migrate + api + collector 2종(naver/weather) + Swagger 문서까지 전부 기동합니다.
- 사용 가능한 profile은 `db` / `api` / `weather` / `naver` / `all` 다섯 가지입니다. api만 필요하면 스크립트 대신 `infisical export`로 `.env`를 만든 뒤 `docker compose --profile api up -d --build`처럼 필요한 profile만 직접 지정합니다.
- Infisical 값을 바꾼 뒤 반영하려면 `run.sh`/`run.ps1`을 다시 실행하거나(내부에서 `.env`를 재생성하므로), 이미 컨테이너가 떠 있다면 `docker compose --profile <프로필> up -d --force-recreate`로 재생성해야 새 값이 실제로 반영됩니다. 단순 재시작만으로는 갱신되지 않습니다.

### Docker 없이 로컬 실행할 때

```shell
infisical run --env=dev -- python manage.py runserver
```

이 경로에서는 시크릿이 프로세스 환경변수로 직접 주입됩니다.
`api/config/settings/base.py`의 `load_dotenv`는 이미 존재하는 환경변수를 덮어쓰지 않으므로(override=False), 우선순위는 **Infisical > `.env` > 코드 기본값** 순입니다.

### 왜 `infisical run -- docker compose up`만으로는 안 되나? (참고)

`infisical run`은 시크릿을 **docker compose 프로세스의 환경변수**로만 주입합니다.
루트 `docker-compose.yml`은 모든 서비스가 `env_file: - .env`로 값을 받으므로, 컨테이너는 `.env` **파일**에서 값을 읽습니다. 프로세스 환경변수는 컨테이너로 자동 전달되지 않습니다.

자세한 내용은 [Q6](#q6-docker-컨테이너에-시크릿이-안-들어가요--옛날-값이-들어가요), [Q7](#q7-docker-compose-up---env-file-infisical-export--명령이-작동하지-않아요) 참고

---

## 4. 인증 방법: 로컬 개발 vs 배포/CI-CD

- Infisical 인증 방식은 **상황에 따라 다름**
- 현재 API key 종료 예정으로 우선적으로 Service Token(free) 사용
- Service Token 생성 위치: Infisical 프로젝트 → **Access Control → Service Tokens → 토큰 생성**
- Machine Identity는 유료

| 상황 | 인증 방법 | 이유 |
| --- | --- | --- |
| **로컬 개발 (팀원 각자)** | `infisical login` — 개인 계정 로그인 | 개인 세션이 인증을 처리, Service Token 불필요 |
| **CI/CD (GitHub Actions 등)** | Service Token 또는 Machine Identity | 사람이 로그인할 수 없는 자동화 환경이므로 토큰 필요 |
| **AWS 서버 자동 배포** | Service Token 또는 Machine Identity | 서버에 사람이 로그인하지 않으므로 토큰 필요 |

### 로컬 개발: `infisical login`이면 충분

팀원은 각자 아래 한 줄만 실행

```shell
infisical login

# 이후 infisical export, infisical run 모두 개인 계정 세션으로 동작
```

> Service Token을 Infisical 안에 저장하는 건 순환 참조 문제가 생깁니다.
> (Infisical 접근에 토큰이 필요한데, 그 토큰이 Infisical 안에 있으면 처음에 접근이 불가)
> **로컬 개발자는 `infisical login`만 하면 됩니다.**

> **참고 — run.sh와 인증의 관계**: `run.sh`/`run.ps1`이 내부에서 실행하는 `infisical export`도 `infisical run`과 인증 방식이 동일합니다. 둘 다 `infisical login`으로 만든 개인 로그인 세션을 그대로 사용합니다. 즉 **Docker로 실행하든 `infisical run`으로 직접 실행하든 로컬 개발자의 인증 방법은 하나(`infisical login`)로 동일**하며, Service Token/Machine Identity는 사람이 로그인할 수 없는 CI/CD·서버 배포 환경에서만 필요합니다.

### 배포/CI-CD: Service Token을 GitHub Secrets 또는 서버 환경변수에 등록

- 자동화 환경(GitHub Actions, AWS 서버)에서는 사람이 로그인할 수 없으므로 Service Token이 필요합니다.
  이 토큰은 **Infisical 안이 아니라** 외부 시스템(GitHub Secrets, 서버 환경변수)에 저장합니다.

```text
GitHub Repository
  → Settings → Secrets and variables → Actions
  → INFISICAL_TOKEN = <서비스 토큰>
```

서버나 CI에서 실행할 때:

```shell
# INFISICAL_TOKEN 환경변수가 설정된 상태에서 실행
infisical export --env=dev --output-file=./.env
docker compose --profile all up -d
```

Service Token 생성 위치: Infisical 프로젝트 → **Access Control → Service Tokens → 토큰 생성**

---

## 5. 필요한 값 & 찾는 곳

| 값 | 우리 팀 값 | 어디서 찾나 (WHERE) | 언제 필요 (WHEN) |
| --- | --- | --- | --- |
| **Organization ID** | `1da5b459-505d-46da-88eb-34c4d5485486` | Infisical 프로젝트 URL의 `/organizations/<Organization ID>/...` 부분. 웹 UI에서는 좌측 하단 조직 메뉴 -> **Organization Settings**에서도 확인 가능 | 비대화형 로그인이나 CI/CD 설정에서만 필요. 일반 로컬 개발자는 보통 몰라도 됩니다 |
| **Project ID** (= `.infisical.json`의 `workspaceId`) | `a9752e10-915a-410e-a714-e89f540ce5e8` | Infisical 프로젝트 URL의 `/projects/secret-management/<Project ID>/overview` 부분. 웹 UI에서는 프로젝트 -> **Settings** 탭의 Project ID에서도 확인 가능 | 레포 밖에서 `--projectId`를 직접 지정할 때 필요. 이 레포 안에서는 `.infisical.json`이 자동 처리합니다 |
| **Environment slug** | `dev` | 프로젝트 대시보드 상단의 환경 탭 | `infisical run --env=<slug>` |
| **Machine Identity Client ID/Secret** | 필요 시 별도 생성 | 조직 -> **Access Control** -> **Identities** | CI/CD, 서버 배포용. 개인 로컬 개발에는 불필요 |

현재 기준 프로젝트 URL:

```text
https://app.infisical.com/organizations/1da5b459-505d-46da-88eb-34c4d5485486/projects/secret-management/a9752e10-915a-410e-a714-e89f540ce5e8/overview
```

---

## 참고: 공식 Infisical 권장 방법 (우리 팀 미사용)

> **우리 팀은 이 방법을 사용하지 않습니다.** 이 섹션은 Infisical 공식 docs가 Docker Compose에 권장하는 "컨테이너 내부에 CLI 설치" 방식입니다. 우리 팀의 실제 방법은 위 "[3. Docker Compose에서 사용하기](#3-docker-compose에서-사용하기-중요)" 섹션을 참고하세요.

공식 docs 참고: [https://infisical.com/docs/integrations/platforms/docker-compose](https://infisical.com/docs/integrations/platforms/docker-compose)

### 1. Docker에 Infisical CLI 설치

- Machine Identity (결제)
- Service Token (Free)

이거는 Service Token 방식

### 2. 토큰 가져오기 (Project → Access Control → Service Tokens)

토큰 생성 후, Docker container에 설치된 CLI에 토큰 공급 및 접근 권한 부여

```shell
# 로컬에서 테스트 (docker run 전용)
docker run --env INFISICAL_TOKEN=<여기에토큰> my-app
```

> **주의:** `docker-compose up --env-file <(infisical export --format=dotenv)` 명령은 사용하지 마세요.
> 이 패턴은 Infisical 공식 docs에서 `docker run` 전용으로, `docker-compose up`에서는 컨테이너의 `env_file:` 항목에 시크릿이 전달되지 않습니다. 또한 프로세스 치환 `<(...)` 구문은 Windows PowerShell에서 동작하지 않습니다.

### 3. Dockerfile 수정

Dockerfile 마지막 줄에 추가

```shell
CMD ["infisical", "run", "--", "[your service start command]"]

# example with single command
CMD ["infisical", "run", "--", "npm", "run", "start"]

# example with multiple commands
CMD ["infisical", "run", "--command", "npm run start && ..."]
```

Docker Compose file

```yaml
# Example Docker Compose file
services:
  web:
    build: .
    image: example-service-1
    environment:
      - INFISICAL_TOKEN=${INFISICAL_TOKEN_FOR_WEB}

  api:
    build: .
    image: example-service-2
    environment:
      - INFISICAL_TOKEN=${INFISICAL_TOKEN_FOR_API}
```

### 4. Docker RUN

| `infisical login` ✅ | `infisical login` ❌ |
| --- | --- |
| 이 단계 Skip | Organization → Access Control → Machine Identities → Create → |

### 5. 셸 변수 내보내기

```shell
# Token refers to the token we generated in step 2 for this service
export INFISICAL_TOKEN_FOR_WEB=<token>

# Token refers to the token we generated in step 2 for this service
export INFISICAL_TOKEN_FOR_API=<token>

# Then run your compose file in the same terminal.
docker-compose ...
```

---

## 6. 트러블슈팅

### Q1. 로그인했는데 프로젝트가 안 보여요

1. EU 리전으로 로그인했을 수 있습니다. `infisical login`을 다시 실행하고 **US (app.infisical.com)** 를 선택
2. 조직에는 들어왔지만 프로젝트 멤버가 아닐 수 있습니다. 관리자에게 `skn28-final-1team` 프로젝트 멤버 추가를 요청

### Q2. 시크릿이 안 들어오는 것 같아요

1. `--env=dev`처럼 올바른 환경을 지정했는지 확인
2. 루트의 `.infisical.json`이 위의 Project ID와 일치하는지 확인
3. 시크릿 값 자체를 터미널에 출력하지 않습니다. 확인이 필요하면 키 이름과 권한/환경 같은 메타데이터만 확인

### Q3. WSL에서 브라우저가 안 열려요

```shell
infisical login -i
```

### Q4. 예전 로그인이 꼬인 것 같아요

```shell
infisical login
```

재로그인 시 US 리전과 올바른 계정을 선택합니다.

### Q5. `.infisical.json`은 어떻게 만들어진 파일인가요?

프로젝트 루트에서 `infisical init`을 실행하면 자동 생성되는 파일입니다.
실행하면 로그인된 계정의 조직/프로젝트 목록이 나오고, 거기서 프로젝트를 선택하면 CLI가 그 프로젝트의 **Project ID**를 `workspaceId`로 기록합니다.
ID를 손으로 입력하는 것이 아니라, 프로젝트를 선택하는 순간 대시보드의 Project ID가 자동으로 들어갑니다.

### Q6. Docker 컨테이너에 시크릿이 안 들어가요 / 옛날 값이 들어가요

컨테이너는 Infisical이 아니라 루트 `.env` 파일에서 값을 읽습니다 (compose의 `env_file` 설정).
다음 순서로 해결합니다.

1. `infisical export --env=dev --output-file=./.env`로 `.env`를 다시 생성
2. `docker compose --profile <프로필> up -d --force-recreate`로 컨테이너를 재생성
3. 자세한 원리는 [3. Docker Compose에서 사용하기](#3-docker-compose에서-사용하기-중요) 참고

### Q7. `docker-compose up --env-file <(infisical export ...)` 명령이 작동하지 않아요

이 명령어는 우리 팀의 실행 방식이 아닙니다. 두 가지 이유로 동작하지 않습니다.

1. **범위 문제:** `docker-compose up --env-file`은 compose.yml 파일 내 `${VAR}` 보간 변수만 처리합니다. 컨테이너의 `env_file: - .env` 항목에는 전달되지 않으므로 시크릿이 컨테이너에 들어가지 않습니다.
2. **호환성 문제:** `<(...)` 프로세스 치환은 bash/zsh 전용으로 Windows PowerShell에서 동작하지 않습니다.

올바른 방법:

```shell
infisical export --env=dev --output-file=./.env
docker compose --profile <프로필> up -d --build
```

또는 레포 루트의 `run.sh`(macOS/Linux) / `run.ps1`(Windows)을 실행하면 위 두 단계가 자동으로 수행됩니다.

### Q8. Service Token을 Infisical 안에 저장해도 될까요?

**안 됩니다.** 순환 참조 문제가 생깁니다.

```text
Infisical 접근하려면 토큰 필요
  → 토큰이 Infisical 안에 있음
  → Infisical 접근하려면 토큰 필요 → 무한 루프
```

로컬 개발자는 `infisical login`으로 개인 계정을 인증하면 되므로 Service Token이 필요 없습니다.
배포/CI-CD 환경의 Service Token은 GitHub Secrets 또는 서버 환경변수에 직접 저장합니다. 자세한 내용은 [4. 인증 방법](#4-인증-방법-로컬-개발-vs-배포ci-cd) 섹션을 참고하세요.

---

## 7. 보안 수칙

1. 시크릿 원문을 카톡, 디스코드, 채팅, 로그, 커밋에 올리지 않습니다.
2. 값 공유가 필요하면 "Infisical dev 환경에 추가했습니다"라고만 알립니다.
3. `.env`는 `infisical export`로만 생성하고 손으로 편집하지 않습니다. `.env`가 `.gitignore`에 포함되어 있는지 확인하고, export 없이 손으로 만든 `.env`는 사용 후 바로 삭제합니다.
4. `.infisical.json`은 프로젝트 식별자만 들어 있으므로 커밋해도 됩니다. 실수로 지우지 않습니다.
5. 시크릿을 유출했다면 즉시 팀에 알리고 해당 키를 재발급(rotate)합니다.
6. Service Token은 팀원끼리 공유하지 않습니다. 로컬 개발은 각자 `infisical login`을 사용합니다.
