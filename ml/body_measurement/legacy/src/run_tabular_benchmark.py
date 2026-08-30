import os
import sys
import pandas as pd
import json
from pathlib import Path

# 프로젝트 루트 등록
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_ROOT))

# 추론용 환경 변수 세팅
os.environ["BODY_MODEL_PATH"] = str(PROJECT_ROOT / "ml" / "body_measurement" / "artifacts" / "models" / "hist_gradient_boosting.joblib")

from ml.body_measurement.src import inference

def run_split_predictions(split_name):
    print(f"=== Running HistGradient predictions for {split_name} ===")
    
    # 1. 대상 데이터 로드
    csv_path = PROJECT_ROOT / "ml" / "body_measurement" / "data" / "splits" / "vlm" / f"{split_name}_set.csv"
    if not csv_path.exists():
        print(f"Error: dataset path not found {csv_path}")
        return
        
    df = pd.read_csv(csv_path)
    
    predictions_rows = []
    
    for idx, row in df.iterrows():
        subj_id = row["subject_id"]
        gender = "male" if row["gender"] == "M" else "female"
        h = float(row["height"])
        w = float(row["weight"])
        
        # Tabular 모델 기반 10개 부위 추정
        pred = inference.estimate_from_basic(gender, h, w)
        
        # 결과 로우 매핑
        res_row = {
            "subject_id": subj_id,
            "status": "success",
            "latency_seconds": 0.005 # 더미 레이턴시
        }
        
        # 10개 키에 맞게 컬럼 분기
        for target, val in pred.items():
            if target.endswith("_ratio"):
                res_row[f"predicted_{target}"] = val
            else:
                res_row[f"predicted_{target}_cm"] = val
                
        predictions_rows.append(res_row)
        
    # 결과 파일 저장 대상 폴더 생성
    exp_dir = PROJECT_ROOT / "ml" / "body_measurement" / "experiments" / "tabular" / split_name
    exp_dir.mkdir(parents=True, exist_ok=True)
    
    pred_df = pd.DataFrame(predictions_rows)
    pred_path = exp_dir / "predictions.csv"
    pred_df.to_csv(pred_path, index=False)
    print(f"Saved predictions to {pred_path}")
    
    # 2. evaluate_results.py 실행
    eval_script = PROJECT_ROOT / "ml" / "body_measurement" / "scripts" / "evaluate_results.py"
    cmd = f"python \"{eval_script}\" --split {split_name} --predictions \"{pred_path}\""
    print(f"Running command: {cmd}")
    os.system(cmd)

if __name__ == "__main__":
    run_split_predictions("validation")
    run_split_predictions("test")
