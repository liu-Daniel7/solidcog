from paddleocr import PaddleOCR

ocr = PaddleOCR(use_angle_cls=True, lang='ch')

img_path = "debug_vertical_tech.png"

result = ocr.ocr(img_path, cls=True)

for line in result[0]:
    print(line[1][0], line[1][1])