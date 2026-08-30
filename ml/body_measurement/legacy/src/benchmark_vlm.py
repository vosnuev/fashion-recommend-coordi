import os
import json
import argparse
import time
import pandas as pd
from PIL import Image
import google.generativeai as genai
from openai import OpenAI

# .env 파일 수동 로드
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                val = val.strip("'\"")
                os.environ[key] = val

# API 키 바인딩
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def build_benchmark_prompt(height, gender, weight):
    gender_full = "Female" if str(gender).upper().startswith("F") else "Male"
    return f"""
    You are an expert in anthropometry and visual body measurement.
    Analyze the provided front-facing photo of a real person.
    
    Known metadata:
    - Height: {height} cm
    - Gender: {gender_full}
    - Weight: {weight} kg
    
    Using these physical descriptors as reference and spatial scale anchors (pixel-to-centimeter calibration based on height and body structure),
    estimate the person's exact body circumferences:
    1. Chest circumference (가슴둘레) in cm
    2. Waist circumference (허리둘레) in cm
    3. Hip circumference (엉덩이둘레) in cm
    
    Format the output strictly as JSON with keys: 'chest', 'waist', and 'hip'.
    Ensure all values are floating-point numbers.
    Do not add any explanations, markdown format blocks, or surrounding text.
    """

def query_gemini(img_path, prompt):
    model = genai.GenerativeModel("gemini-1.5-flash")
    img = Image.open(img_path)
    response = model.generate_content(
        [prompt, img],
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text.strip())

def query_openai_direct(img_path, prompt, model_id):
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    import base64
    with open(img_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        
    response = client.chat.completions.create(
        model=model_id,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
    )
    res_text = response.choices[0].message.content
    return json.loads(res_text.strip())

def query_openrouter(img_path, prompt, model_id):
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    
    import base64
    with open(img_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        
    response = client.chat.completions.create(
        model=model_id,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
    )
    res_text = response.choices[0].message.content
    return json.loads(res_text.strip())

def main():
    parser = argparse.ArgumentParser(description="VLM Body Measurement Benchmarker")
    parser.add_argument("--model", type=str, default="gpt4o-mini", choices=["gemini", "qwen", "internvl", "gpt4o-mini"],
                        help="VLM Model to benchmark")
    parser.add_argument("--dataset", type=str, default="external", choices=["external", "raw"],
                        help="Dataset to evaluate: 'external' (UniqueData) or 'raw' (SizeKorea)")
    parser.add_argument("--limit", type=int, default=5, help="Number of subjects to test")
    args = parser.parse_args()

    if args.dataset == "external":
        meta_path = "ml/body_measurement/data/external_samples/summary_external_samples.csv"
    else:
        meta_path = "ml/body_measurement/data/labels/sizekorea_vlm_182_labels.csv"

    if not os.path.exists(meta_path):
        print(f"오류: 수집된 메타데이터를 찾을 수 없습니다 -> {meta_path}")
        return
        
    df = pd.read_csv(meta_path)
    limit = min(args.limit, len(df))
    test_df = df.head(limit)
    
    print(f"\n=== {args.model.upper()} VLM 성능 계측 벤치마크 시작 (대상 데이터: {args.dataset.upper()} | 표본 수: {limit}명) ===")
    
    results = []
    
    for idx, row in test_df.iterrows():
        sub_id = row["subject_id"]
        gender = row["gender"]
        weight = row["weight"]
        height = row["height"]
        
        actual_chest = row["chest"]
        actual_waist = row["waist"]
        actual_hip = row["hip"]
        
        img_path = row["image_path"]
        
        print(f"[{sub_id}] 계측 수행 중... (입력 조건 - 성별: {gender}, 키: {height}cm, 몸무게: {weight}kg)")
        
        prompt = build_benchmark_prompt(height, gender, weight)
        
        try:
            start_time = time.time()
            if args.model == "gemini":
                pred = query_gemini(img_path, prompt)
            elif args.model == "gpt4o-mini":
                # 로컬 OpenAI API 키가 있을 경우 직접 호출로 동작 보완
                if OPENAI_API_KEY:
                    pred = query_openai_direct(img_path, prompt, "gpt-4o-mini")
                else:
                    pred = query_openrouter(img_path, prompt, "openai/gpt-4o-mini")
            elif args.model == "qwen":
                pred = query_openrouter(img_path, prompt, "qwen/qwen2.5-vl-72b-instruct")
            elif args.model == "internvl":
                pred = query_openrouter(img_path, prompt, "opengvlab/internvl3-78b")
                
            latency = time.time() - start_time
            
            p_chest = float(pred.get("chest", 0.0))
            p_waist = float(pred.get("waist", 0.0))
            p_hip = float(pred.get("hip", 0.0))
            
            err_chest = abs(p_chest - actual_chest)
            err_waist = abs(p_waist - actual_waist)
            err_hip = abs(p_hip - actual_hip)
            
            results.append({
                "subject_id": sub_id,
                "gender": gender,
                "weight": weight,
                "height": height,
                "actual_chest": actual_chest,
                "pred_chest": p_chest,
                "err_chest": err_chest,
                "actual_waist": actual_waist,
                "pred_waist": p_waist,
                "err_waist": err_waist,
                "actual_hip": actual_hip,
                "pred_hip": p_hip,
                "err_hip": err_hip,
                "latency_sec": round(latency, 2)
            })
            
            print(f"-> 계측 성공 (Latency: {latency:.2f}s) | 오차 - 가슴: {err_chest:.1f}cm, 허리: {err_waist:.1f}cm, 엉덩이: {err_hip:.1f}cm")
            
        except Exception as e:
            print(f"-> [{sub_id}] 계측 실패: {e}")
            
    if results:
        res_df = pd.DataFrame(results)
        report_dir = "ml/body_measurement/reports"
        os.makedirs(report_dir, exist_ok=True)
        
        output_csv = os.path.join(report_dir, f"benchmark_{args.dataset}_{args.model}.csv")
        res_df.to_csv(output_csv, index=False, encoding="utf-8-sig")
        
        print("\n=== 벤치마크 최종 요약 리포트 ===")
        print(f"가슴둘레 평균 절대 오차 (MAE): {res_df['err_chest'].mean():.2f} cm")
        print(f"허리둘레 평균 절대 오차 (MAE): {res_df['err_waist'].mean():.2f} cm")
        print(f"엉덩이둘레 평균 절대 오차 (MAE): {res_df['err_hip'].mean():.2f} cm")
        print(f"평균 응답 지연 시간 (Latency): {res_df['latency_sec'].mean():.2f} 초")
        print(f"결과 리포트 파일 저장 위치: {output_csv}")
    else:
        print("모든 계측에 실패하여 리포트를 생성하지 못했습니다.")

if __name__ == "__main__":
    main()
