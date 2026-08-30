"""SAM 3 가중치(sam3.pt) 다운로드 스크립트.

facebook/sam3는 gated 저장소라 액세스 승인 + HF 토큰 인증이 필요하다.
승인된 계정의 토큰을 HF_TOKEN 환경변수로 넘기거나,
`hf auth login`(구 huggingface-cli login)으로 미리 로그인해 둔다.

실행: pip install -U huggingface_hub
      HF_TOKEN=hf_xxx python download_sam3.py
저장 위치: test/sam3/sam3.pt (이 파일 기준 상대 경로 → 어디서 실행해도 동일)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ID = "facebook/sam3"
FILENAME = "sam3.pt"
# 이 파일이 test/sam3/ 안에 있으므로 같은 폴더에 저장한다.
DEST_DIR = Path(__file__).resolve().parent


def download(repo_id: str, filename: str, dest_dir: Path) -> Path:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

    dest_dir.mkdir(parents=True, exist_ok=True)
    token = os.getenv("HF_TOKEN")  # 없으면 `hf auth login` 캐시 자격증명 사용

    try:
        path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=dest_dir,     # 캐시 대신 실제 파일을 이 폴더에 저장
            token=token,
        )
    except GatedRepoError:
        sys.exit(
            f"[오류] {repo_id} 접근 권한이 없습니다.\n"
            f"  1) https://huggingface.co/{repo_id} 에서 액세스 승인 여부 확인\n"
            f"  2) 승인받은 계정의 토큰을 HF_TOKEN으로 지정했는지 확인"
        )
    except RepositoryNotFoundError:
        sys.exit(
            f"[오류] 저장소를 찾을 수 없습니다: {repo_id}\n"
            f"  토큰 미인증 시 gated 저장소가 404로 보일 수 있습니다. HF_TOKEN을 확인하세요."
        )
    return Path(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=REPO_ID, help="예: facebook/sam3.1")
    ap.add_argument("--filename", default=FILENAME, help="예: sam3.1.pt")
    ap.add_argument("--dest", default=str(DEST_DIR))
    args = ap.parse_args()

    path = download(args.repo, args.filename, Path(args.dest))
    size_gb = path.stat().st_size / 1024**3
    print(f"완료: {path} ({size_gb:.2f} GB)")
    if size_gb < 1:  # sam3.pt는 약 3.4GB — 비정상적으로 작으면 재다운로드 안내
        print("[경고] 파일 크기가 예상(약 3.4GB)보다 작습니다. 다운로드 중단 여부를 확인하세요.")


if __name__ == "__main__":
    main()
