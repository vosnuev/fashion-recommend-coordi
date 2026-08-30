"""
여자 5체형 × Plus-Size 비교 합본 이미지 생성기.

입력: golden-set/body/images/comparison/_pair_1_inv.png ... _pair_5_round.png
       (각 pair는 1024x1024, 좌측 slim + 우측 plus)
출력: golden-set/body/images/comparison/female-5shapes-plus-size.png

본인 5체형 분류(02-body-proportion-rules.md) × Pinterest plus-size 6체형의 통합 차트.
"""
from PIL import Image, ImageDraw, ImageFont
import os

BASE_DIR = r'C:\Users\Playdata\Desktop\SKN28-FINAL-1Team\docs\golden-set\images\body-proportions\comparison'

# 5체형 정보: (pair 파일, 한글 라벨, 영문 라벨, 비율 신호, plus-size 매핑 메모)
SHAPES = [
    ('_pair_1_inv.png',  '역삼각형',  'Inverted Triangle',  'shoulder/hip 상위 33%',                '↔ Pinterest Inverted'),
    ('_pair_2_tri.png',  '삼각형',    'Pear / Triangle',    'shoulder/hip 하위 33%',                '↔ Pinterest Pear'),
    ('_pair_3_hour.png', '모래시계형', 'Hourglass',          'waist/hip 하위 33% + 균형',            '↔ Pinterest Hourglass'),
    ('_pair_4_rect.png', '직사각형',  'Rectangle',          'shoulder/hip·waist/hip 중간',          '↔ Pinterest Rectangle'),
    ('_pair_5_round.png','라운드형',  'Round (Apple / Oval)','waist/hip 상위 33%',                  'Apple / Oval sub-type'),
]

# 처방 요약 (가로축만, 기본값 balanced 가정 시 세로축 미적용)
RX = [
    ['상의: 레귤러·슬림 (오버핏 기피)',  '하의: 와이드·레귤러 (슬림 기피)',  '기장: 기본',                '색: 상의 축소색, 하의 확장색'],
    ['상의: 오버·레귤러 (슬림 기피)',    '하의: 레귤러 (와이드 기피)',       '기장: 기본',                '색: 상의 확장색, 하의 축소색'],
    ['상의: 레귤러·슬림 (오버핏 기피)',  '하의: 모두 OK',                    '기장: 크롭~기본',           '색: 허리 강조 (허리 노출)'],
    ['상의: 레귤러·오버',                '하의: 와이드·레귤러',              '기장: 크롭~기본',           '색: 상하 명도차 ≥18 (허리선 인공 생성)'],
    ['상의: 레귤러 (슬림·오버 기피)',    '하의: 레귤러·와이드 (슬림 기피)',  '기장: 크롭 기피',           '색: 상하 동일 계열, 상의 축소색'],
]

# 폰트
FONT = r'C:\Windows\Fonts\malgun.ttf'
title_font   = ImageFont.truetype(FONT, 56)
subtitle_font= ImageFont.truetype(FONT, 30)
col_font     = ImageFont.truetype(FONT, 34)
label_font   = ImageFont.truetype(FONT, 36)
en_font      = ImageFont.truetype(FONT, 22)
ratio_font   = ImageFont.truetype(FONT, 22)
rx_font      = ImageFont.truetype(FONT, 20)
small_font   = ImageFont.truetype(FONT, 20)
footer_font  = ImageFont.truetype(FONT, 26)
note_font    = ImageFont.truetype(FONT, 22)

# pair 이미지 로드 + 좌우 crop
pair_imgs = []
for f, *_ in SHAPES:
    img = Image.open(os.path.join(BASE_DIR, f))
    w, h = img.size
    slim = img.crop((0, 0, w // 2, h))
    plus = img.crop((w // 2, 0, w, h))
    pair_imgs.append((slim, plus))

# 캔버스 레이아웃
CELL_W = 460           # silhouette 한 칸 너비
CELL_H = 880           # silhouette 한 칸 높이
LABEL_W = 280          # 좌측 라벨 열
RX_W   = 540           # 우측 처방 열
HEADER_H = 200         # 상단 제목/열 헤더 영역
FOOTER_H = 760         # 하단 세로축 + plus-size 인사이트 영역
MARGIN  = 30

canvas_w = LABEL_W + CELL_W * 2 + RX_W + MARGIN * 2
canvas_h = HEADER_H + CELL_H * len(SHAPES) + FOOTER_H + MARGIN * 2

canvas = Image.new('RGB', (canvas_w, canvas_h), (252, 248, 240))
draw = ImageDraw.Draw(canvas)

# === 1. 상단 헤더 ===
draw.text((MARGIN + 20, 30), '여자 5체형 × Plus-Size 비교', fill=(35, 35, 55), font=title_font)
draw.text((MARGIN + 24, 110), '동일 비율 신호, 다른 볼륨  |  가로축(체형 5종) × Plus-Size 통합', fill=(100, 100, 130), font=subtitle_font)

# 열 헤더
slim_col_x  = LABEL_W
plus_col_x  = LABEL_W + CELL_W
rx_col_x    = LABEL_W + CELL_W * 2
draw.text((slim_col_x + CELL_W//2 - 60, HEADER_H - 50), 'SLIM',      fill=(80, 80, 110), font=col_font)
draw.text((plus_col_x + CELL_W//2 - 110, HEADER_H - 50), 'PLUS-SIZE', fill=(80, 80, 110), font=col_font)
draw.text((rx_col_x + 20, HEADER_H - 50), '처방 요약 (가로축)', fill=(80, 80, 110), font=col_font)

# 열 구분선
for x in [LABEL_W, LABEL_W + CELL_W, LABEL_W + CELL_W * 2]:
    draw.line([(x, HEADER_H - 60), (x, HEADER_H + CELL_H * len(SHAPES))], fill=(220, 215, 205), width=1)

# === 2. 5행 본문 ===
for i, ((slim, plus), (f, ko, en, ratio, note), rx) in enumerate(zip(pair_imgs, SHAPES, RX)):
    y = HEADER_H + i * CELL_H
    # 행 구분선
    if i > 0:
        draw.line([(MARGIN, y), (canvas_w - MARGIN, y)], fill=(235, 230, 220), width=1)

    # 좌측 라벨
    draw.text((MARGIN + 20, y + 30),  ko,   fill=(30, 30, 50),  font=label_font)
    draw.text((MARGIN + 20, y + 80),  en,   fill=(120, 120, 140), font=en_font)
    draw.text((MARGIN + 20, y + 115), ratio, fill=(80, 80, 100), font=ratio_font)

    # plus-size 매핑 메모 (작게)
    draw.text((MARGIN + 20, y + CELL_H - 110), note, fill=(150, 100, 80), font=ratio_font)

    # 실루엣
    canvas.paste(slim.resize((CELL_W, CELL_H)), (slim_col_x, y))
    canvas.paste(plus.resize((CELL_W, CELL_H)), (plus_col_x, y))

    # 처방
    rx_y = y + 30
    for j, line in enumerate(rx):
        draw.text((rx_col_x + 20, rx_y + j * 38), '• ' + line, fill=(60, 60, 85), font=rx_font)

# === 3. 하단 푸터 ===
fy = HEADER_H + CELL_H * len(SHAPES) + MARGIN
draw.line([(MARGIN, fy), (canvas_w - MARGIN, fy)], fill=(200, 190, 175), width=2)

draw.text((MARGIN + 20, fy + 20), '세로축 3개 비율 (기본 torso baseline 0.786 → balanced, 사진 VLM 유효값 사용)', fill=(35, 35, 55), font=footer_font)

draw.text((MARGIN + 20, fy + 70),  '① 상체 : 하체 (torso_ratio)', fill=(50, 50, 75), font=note_font)
draw.text((MARGIN + 280, fy + 70), '— 상체 김: 크롭티 + 하이웨스트 (사용자 예시) / 상체 짧음: 롱 상의, 로우라이즈', fill=(70, 70, 95), font=small_font)

draw.text((MARGIN + 20, fy + 110), '② 목 길이 (neck_length)',     fill=(50, 50, 75), font=note_font)
draw.text((MARGIN + 280, fy + 110), '— 긴 목: 하이넥·터틀넥 OK / 짧은 목: V넥·U넥·민소매로 시각 길이 보완', fill=(70, 70, 95), font=small_font)

draw.text((MARGIN + 20, fy + 150), '③ 허벅지 : 종아리 (thigh_calf_ratio)', fill=(50, 50, 75), font=note_font)
draw.text((MARGIN + 280, fy + 150), '— 허벅지 김: 와이드·부츠컷 (무릎 위 분산) / 종아리 김: 슬림·스트레이트 (무릎 아래 분산)', fill=(70, 70, 95), font=small_font)

# Plus-Size 인사이트
draw.text((MARGIN + 20, fy + 210), 'Plus-Size 가이드 통합 인사이트 (Pinterest 출처)', fill=(150, 80, 70), font=footer_font)
draw.text((MARGIN + 20, fy + 260), '• 라운드형은 Apple(어깨·허리 발달) + Oval(전신 둥글) 두 sub-type으로 분화 → 같은 waist/hip ≥ p67 처방이지만 강조점이 다름', fill=(60, 60, 85), font=small_font)
draw.text((MARGIN + 20, fy + 290), '• Hourglass / Pear / Inverted Triangle / Rectangle은 본 분류와 직접 일치 → 4종은 ratio 신호만으로 충분', fill=(60, 60, 85), font=small_font)
draw.text((MARGIN + 20, fy + 320), '• 같은 체형이라도 BMI 구간(아시아-태평양 WHO 5구간)에 따라 볼륨이 다름 → 5종 × 5구간 매트릭스로 확장 가능', fill=(60, 60, 85), font=small_font)
draw.text((MARGIN + 20, fy + 350), '• 처방 원칙: 가로축 = "비율" 처방 / Plus-Size = "볼륨" 가중치 (핏·기장 미세 조정)', fill=(60, 60, 85), font=small_font)

draw.text((MARGIN + 20, fy + 420), '참조: 02-body-proportion-rules.md (가로축 5분류, 세로축 3비율) · rules/body_fit_rules.json · Pinterest plus-size 6체형', fill=(130, 130, 150), font=small_font)

# 저장
out_path = os.path.join(BASE_DIR, 'female-5shapes-plus-size.png')
canvas.save(out_path, 'PNG', optimize=True)
print(f'Saved: {out_path}')
print(f'Size: {canvas.size}')
