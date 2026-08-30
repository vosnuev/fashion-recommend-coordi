"""이 프로세스가 **어느 코드로 돌고 있는지** 한 줄로 말한다.

이 프로젝트에서 가장 자주 난 실패는 로직 버그가 아니라 배포 편차였다. api와
워커가 각각 다른 이미지로 빌드되는 바람에, `docker compose build api` 뒤에도
daily-look-worker는 몇 시간 전 코드를 그대로 물고 돌았다. 그런데 겉으로 보이는
증상은 완벽하게 "추천 로직이 틀렸다"였다 — API가 쓴 스냅샷에는 성별이 제대로
들어 있는데(새 코드) 추천 결과만 성별을 무시했으니(옛 코드), 코드를 아무리
읽어도 원인이 안 나온다.

그래서 컨테이너마다 자기 코드의 지문을 찍게 한다. api와 워커의 지문이 다르면
그건 로직 문제가 아니라 배포 문제다. 그 한 줄이 몇 시간을 줄인다.

git SHA를 쓰지 않는 이유: 이미지에 .git이 없고, 빌드 인자로 넣으면 넣는 걸
잊었을 때 조용히 거짓말을 한다. 실제로 로드된 소스를 해싱하면 거짓말을 못 한다.
"""

from __future__ import annotations

import hashlib
import inspect
from typing import Iterable

#: 지문에 넣을 모듈. 추천 결과를 결정하는 것들만 고른다. 여기 없는 파일이
#: 바뀌어도 지문은 그대로다 — 목적이 "빌드 식별"이 아니라 "추천 코드 식별"이라
#: 무관한 변경으로 값이 흔들리지 않는 편이 낫다.
TRACKED_MODULES = (
    "apps.recommend.services.retriever",
    "apps.recommend.services.gender",
    "apps.recommend.services.daily_look",
    "apps.recommend.services.outfit_context",
    "apps.recommend.services.body_profile",
)


def _sources(names: Iterable[str]) -> list[tuple[str, str]]:
    import importlib

    out: list[tuple[str, str]] = []
    for name in names:
        try:
            module = importlib.import_module(name)
            source = inspect.getsource(module)
        except Exception:  # noqa: BLE001 — 지문 때문에 프로세스가 죽으면 안 된다
            source = "(unavailable)"
        out.append((name, source))
    return out


def code_fingerprint(names: Iterable[str] = TRACKED_MODULES) -> str:
    """추천 코드의 짧은 지문. 같은 코드면 같은 값이 나온다."""
    digest = hashlib.sha1()
    for name, source in _sources(names):
        digest.update(name.encode())
        digest.update(hashlib.sha1(source.encode()).digest())
    return digest.hexdigest()[:12]


def capabilities() -> dict[str, bool]:
    """지문만으로는 "무엇이 다른지" 모른다. 중요한 동작 몇 개는 직접 실행해 본다.

    소스를 문자열로 뒤지지 않고 호출한다. 컨테이너 안 파일은 이미지에 구워진
    것이라 파일을 읽는 방식으로는 "옛 이미지"를 못 잡아낸다.
    """
    checks: dict[str, bool] = {}
    try:
        from apps.recommend.services.retriever import RetrievalRequest, build_filter

        built = build_filter(RetrievalRequest(gender="male"))
        checks["gender_hard_filter"] = any(
            getattr(c, "key", "") == "presentation_group"
            for c in (getattr(built, "must", None) or [])
        )
    except Exception:  # noqa: BLE001
        checks["gender_hard_filter"] = False
    return checks


def describe() -> str:
    """로그·진단 출력에 그대로 쓸 한 줄."""
    flags = ", ".join(
        f"{name}={'있음' if ok else '없음'}" for name, ok in sorted(capabilities().items())
    )
    return f"code={code_fingerprint()} ({flags})"
