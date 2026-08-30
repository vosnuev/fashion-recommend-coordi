# 세그멘테이션 모델 비교 테스트 통합 이미지 (GPU)
# segformer / sam2(grounded) / sam3(+gemini) 3개 테스트를 한 이미지에서 실행한다.
#
# 빌드:  docker build -t seg-tests .          (test/ 폴더에서)
# 실행:  ./run_docker.sh                      (input/의 jpg 전체 → output/)
#        ./run_docker.sh segformer|sam2|sam3  (특정 테스트만)
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

# git: 공식 Meta SAM2(git+https) 설치용 / libgl1 등: opencv 런타임
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libxcb1 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 레이어를 소스와 분리해 코드 수정 시 재설치 방지.
# --no-build-isolation: SAM-2 빌드가 베이스 이미지의 torch를 그대로 쓰도록 함
#   (빌드 격리 시 torch를 새로 내려받아 빌드가 무거워지거나 실패한다).
COPY requirements.txt .
RUN pip install --no-cache-dir --no-build-isolation -r requirements.txt

COPY common/ common/
COPY segformer/ segformer/
COPY sam2/ sam2/
COPY sam3/ sam3/
COPY run_all_tests.sh .
RUN chmod +x run_all_tests.sh

# 런타임 마운트 (run_docker.sh 참고):
#   -v test/input:/app/input:ro       입력 jpg
#   -v test/output:/app/output        결과 회수
#   -v test/sam3/sam3.pt:/app/sam3/sam3.pt:ro   SAM3 가중치 (HF gated → 이미지 미포함)
#   -e GEMINI_API_KEY                 sam3 테스트 필수
ENTRYPOINT ["./run_all_tests.sh"]
