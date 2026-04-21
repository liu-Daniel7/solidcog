from paddleocr import PaddleOCR

# 初始化时指定v3模型，自动下载工业场景预训练权重
print("开始下载PP-OCRv3模型...")
ocr = PaddleOCR(use_angle_cls=True, lang='ch', version='PP-OCRv3')
print("PP-OCRv3模型下载完成！")