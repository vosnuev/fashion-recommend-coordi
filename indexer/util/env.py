"""프로젝트 루트 .env 로더 (컨테이너·리포 체크아웃 공용).

`.env` 위치를 `Path(__file__).parent.parent` 처럼 고정 인덱스로 잡으면
실행 위치에 따라 깨진다.

- 리포 체크아웃: `indexer/product_indexer/product_config.py` 기준으로
  두 단계 위가 루트다. 패키지 구조가 바뀌면 인덱스도 같이 바뀌어야 한다.
- 컨테이너: 이미지 안에는 `/app/product_indexer/...`만 있고 루트 `.env`는
  아예 복사되지 않는다. docker compose가 `env_file: .env`로 값을 컨테이너
  환경변수에 직접 주입하므로 파일을 찾지 못하는 게 정상이다.

그래서 (1) ENV_FILE 명시 지정 → (2) 상위 디렉터리 탐색 → (3) 조용히 통과
순으로 처리하고, 항상 `override=False`로 읽어 compose가 주입한 환경변수를
파일 값이 덮어쓰지 않게 한다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 리포에서 실행할 때 호출 모듈 기준으로 거슬러 올라갈 최대 단계.
# 무한정 올라가면 리포 바깥의 남의 .env를 잘못 읽을 수 있다.
_MAX_PARENT_DEPTH = 5


def load_project_env(start: str | Path | None = None) -> Path | None:
    """루트 `.env`를 찾아 로드하고 그 경로를 반환한다 (없으면 None).

    Args:
        start: 탐색 기준 파일 경로. 보통 호출 모듈의 `__file__`을 넘긴다.
    """
    explicit = os.getenv("ENV_FILE", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            load_dotenv(path, override=False)
            return path
        logger.warning("ENV_FILE 경로에 파일이 없습니다: %s", path)
        return None

    origin = Path(start).resolve() if start else Path(__file__).resolve()
    for parent in list(origin.parents)[:_MAX_PARENT_DEPTH]:
        candidate = parent / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate

    # 컨테이너 실행 경로. compose env_file / --env-file로 이미 주입되어 있다.
    return None
