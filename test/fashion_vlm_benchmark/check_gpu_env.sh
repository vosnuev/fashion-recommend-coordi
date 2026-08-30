#!/usr/bin/env bash

# 모델을 설치하기 전에 PuTTY로 접속한 GPU 서버에서 실행한다.
# 시크릿이나 전체 환경변수는 출력하지 않는다.

set -u

section() {
  printf '\n[%s]\n' "$1"
}

section "timestamp"
date --iso-8601=seconds 2>/dev/null || date

section "system"
uname -a

section "gpu"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi
  printf '\nGPU summary\n'
  nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu --format=csv
  printf '\nGPU compute processes\n'
  nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv 2>/dev/null || true
else
  echo "nvidia-smi를 찾을 수 없습니다."
fi

section "disk"
df -h

section "memory"
free -h 2>/dev/null || true

section "conda"
if command -v conda >/dev/null 2>&1; then
  conda env list
else
  echo "conda를 찾을 수 없습니다."
fi

section "python"
if command -v python >/dev/null 2>&1; then
  python --version
  python - <<'PY'
try:
    import torch
except ImportError:
    print("torch: not installed")
else:
    print(f"torch: {torch.__version__}")
    print(f"cuda_available: {torch.cuda.is_available()}")
    print(f"cuda_device_count: {torch.cuda.device_count()}")
    for index in range(torch.cuda.device_count()):
        properties = torch.cuda.get_device_properties(index)
        print(
            f"cuda_device_{index}: {properties.name}, "
            f"vram={properties.total_memory / 1024**3:.2f} GiB"
        )
PY
else
  echo "python을 찾을 수 없습니다."
fi

