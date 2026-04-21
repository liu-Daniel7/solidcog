import requests
import json

QWEN_API_KEY = "REDACTED_QWEN_API_KEY"

# 测试1：简单的文本对话（不需要图片）
print("=" * 50)
print("测试1：简单的文本对话API")
print("=" * 50)

url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
headers = {
    "Authorization": f"Bearer {QWEN_API_KEY}",
    "Content-Type": "application/json"
}

data = {
    "model": "qwen-vl-plus",
    "messages": [
        {
            "role": "user",
            "content": "你好，请简单介绍一下自己。"
        }
    ],
    "stream": False
}

print(f"请求URL: {url}")
print(f"模型: {data['model']}")
print("正在发送请求...")

try:
    response = requests.post(url, headers=headers, json=data, timeout=60)
    print(f"响应状态码: {response.status_code}")
    print(f"响应头: {dict(response.headers)}")
    print(f"响应内容: {response.text[:500]}")

    if response.status_code == 200:
        result = response.json()
        print("\n✅ API调用成功！")
        print(f"模型: {result.get('model', 'unknown')}")
        content = result["choices"][0]["message"]["content"]
        print(f"回复: {content}")
    else:
        print(f"\n❌ 调用失败: {response.text}")

except Exception as e:
    print(f"\n❌ 异常: {type(e).__name__}: {e}")

# 测试2：检查API密钥是否有效
print("\n" + "=" * 50)
print("测试2：检查API密钥有效性")
print("=" * 50)

# 尝试用不同的模型
models_to_test = [
    "qwen-vl-plus",
    "qwen-vl-max",
    "qwen-vl-chat",
    "qwen-plus",
    "qwen-chat"
]

for model in models_to_test:
    print(f"\n尝试模型: {model}")
    data["model"] = model
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            print(f"  ✅ {model} - 可用")
            result = response.json()
            print(f"  回复: {result['choices'][0]['message']['content'][:100]}...")
            break
        elif response.status_code == 401:
            print(f"  ❌ {model} - 认证失败 (401)")
        elif response.status_code == 404:
            print(f"  ❌ {model} - 端点不存在 (404)")
        elif response.status_code == 400:
            print(f"  ❌ {model} - 请求错误 (400): {response.text[:100]}")
        else:
            print(f"  ❌ {model} - 状态码 {response.status_code}: {response.text[:100]}")
    except Exception as e:
        print(f"  ❌ {model} - 异常: {str(e)[:50]}")