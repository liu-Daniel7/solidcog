import requests
import base64
import os

QWEN_API_KEY = "REDACTED_QWEN_API_KEY"
QWEN_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

# 尝试多个可能的端点
ENDPOINTS = [
    "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
    "https://ark.cn-beijing.volces.com/api/v2/chat/completions",
    "https://ark.cn-beijing.volces.com/api/v1/chat/completions",
]

def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

def test_qwen_vl():
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
        print("未找到测试图片")
        return

    print(f"使用测试图片: {image_path}")

    # 编码图片
    image_base64 = encode_image_to_base64(image_path)

    # 构建请求
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "qwen-vl-plus",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请分析这张图纸，简要说明主要内容。"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        }
                    }
                ]
            }
        ],
        "stream": False
    }

    print("正在调用千问VL API...")

    # 尝试不同的端点
    for endpoint in ENDPOINTS:
        print(f"\n尝试端点: {endpoint}")
        try:
            response = requests.post(endpoint, headers=headers, json=data, timeout=120)
            print(f"响应状态码: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                print("✅ 千问VL API调用成功！")
                print(f"模型: {result.get('model', 'unknown')}")
                content = result["choices"][0]["message"]["content"]
                print(f"分析结果: {content[:500]}...")
                return True
            elif response.status_code == 401:
                print(f"❌ 认证失败: {response.text[:200]}")
            elif response.status_code == 404:
                print(f"❌ 端点不存在，继续尝试下一个...")
            else:
                print(f"❌ 调用失败: {response.text[:200]}")
        except Exception as e:
            print(f"❌ 请求异常: {str(e)[:100]}")
            continue

    print("\n所有端点都尝试失败，请检查API密钥和端点配置")
    return False

if __name__ == "__main__":
    test_qwen_vl()