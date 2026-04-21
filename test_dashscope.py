from openai import OpenAI
import os

QWEN_API_KEY = "sk-9538f68cbac8442f8a568ba13d6bffc6"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

print("=" * 50)
print("测试阿里云DashScope千问VL API")
print("=" * 50)

# 初始化OpenAI客户端
client = OpenAI(
    api_key=QWEN_API_KEY,
    base_url=QWEN_BASE_URL
)

# 测试1：简单的文本对话
print("\n测试1：简单的文本对话")
print("-" * 50)

try:
    completion = client.chat.completions.create(
        model="qwen-vl-plus",
        messages=[
            {
                "role": "user",
                "content": "你好，请简单介绍一下自己。"
            }
        ],
        stream=False
    )
    print("✅ 文本对话成功！")
    print(f"回复: {completion.choices[0].message.content}")
except Exception as e:
    print(f"❌ 文本对话失败: {e}")

# 测试2：带图片的分析
print("\n测试2：带图片的图纸分析")
print("-" * 50)

# 找一个测试图片
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
    print("未找到测试图片，跳过图片分析测试")
else:
    print(f"使用测试图片: {image_path}")

    # 读取图片并转为base64
    import base64
    with open(image_path, "rb") as image_file:
        image_base64 = base64.b64encode(image_file.read()).decode("utf-8")

    try:
        completion = client.chat.completions.create(
            model="qwen-vl-plus",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请简要分析这张图纸的主要内容。"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            stream=False
        )
        print("✅ 图片分析成功！")
        print(f"回复: {completion.choices[0].message.content[:500]}...")
    except Exception as e:
        print(f"❌ 图片分析失败: {e}")

print("\n" + "=" * 50)
print("测试完成")
print("=" * 50)