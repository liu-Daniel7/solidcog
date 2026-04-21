import os
import sys

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import ocr_with_qwen_vl, USE_QWEN_VL_OCR

print("=" * 60)
print("测试千问VL OCR功能")
print("=" * 60)

print(f"当前OCR模式: {'千问VL' if USE_QWEN_VL_OCR else 'PaddleOCR'}")

# 测试图片路径
test_images = [
    "debug_vertical_tech.png",
    "debug_vertical_title.png",
    "uploads/20260418171234567890_test.png"
]

image_path = None
for img in test_images:
    if os.path.exists(img):
        image_path = img
        break

if not image_path:
    print("❌ 未找到测试图片")
    sys.exit(1)

print(f"使用测试图片: {image_path}")
print()

# 测试千问VL OCR
print("测试千问VL OCR...")
try:
    result = ocr_with_qwen_vl(image_path)
    
    print("✅ 千问VL OCR成功！")
    print(f"使用模型: {result.get('model_used', 'unknown')}")
    print()
    
    # 打印结果
    print("【标题栏】")
    print(result.get('title_block', '无'))
    print()
    
    print("【技术要求】")
    print(result.get('tech_block', '无'))
    print()
    
    print("【完整结果】")
    print(result.get('all_text', '无')[:500] + "...")
    print()
    
    print(f"识别完成，标题栏: {len(result.get('title_block', ''))} 字符")
    print(f"技术要求: {len(result.get('tech_block', ''))} 字符")
    print(f"完整结果: {len(result.get('all_text', ''))} 字符")
    
except Exception as e:
    print(f"❌ 千问VL OCR失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)