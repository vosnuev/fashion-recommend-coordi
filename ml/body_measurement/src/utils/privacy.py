import cv2
import os

def blur_face(image_path, output_path, ksize=(99, 99), sigma=30):
    """
    OpenCV Haar Cascade를 사용하여 이미지 내의 얼굴을 검출하고 강력한 가우시안 블러를 적용합니다.
    얼굴 검출 실패 시 상단 중앙 영역을 강제 블러하는 Heuristic Fallback 장치를 내장하고 있습니다.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"이미지를 불러올 수 없습니다: {image_path}")
        
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Haar Cascade 정면 얼굴 가속 검출기 로드
    cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    face_cascade = cv2.CascadeClassifier(cascade_path)
    
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(60, 60)
    )
    
    h, w, _ = img.shape
    
    if len(faces) > 0:
        # 얼굴 검출 성공 시 해당 영역들 블러 처리
        for (x, y, fw, fh) in faces:
            # 살짝 여유를 두어 얼굴 전체 커버하도록 마진 추가
            margin_x = int(fw * 0.15)
            margin_y = int(fh * 0.15)
            
            x1 = max(0, x - margin_x)
            y1 = max(0, y - margin_y)
            x2 = min(w, x + fw + margin_x)
            y2 = min(h, y + fh + margin_y)
            
            roi = img[y1:y2, x1:x2]
            blurred_roi = cv2.GaussianBlur(roi, ksize, sigma)
            img[y1:y2, x1:x2] = blurred_roi
        detected = True
    else:
        # 실패 시 Fail-safe Heuristic: 전신 사진 규격 상 얼굴이 무조건 존재하는 상단 10%~25%, 좌우 35%~65% 영역 강제 블러링
        y1 = int(h * 0.05)
        y2 = int(h * 0.25)
        x1 = int(w * 0.30)
        x2 = int(w * 0.70)
        
        roi = img[y1:y2, x1:x2]
        blurred_roi = cv2.GaussianBlur(roi, ksize, sigma)
        img[y1:y2, x1:x2] = blurred_roi
        detected = False
        
    cv2.imwrite(output_path, img)
    return detected
