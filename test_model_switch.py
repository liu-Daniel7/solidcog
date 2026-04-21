import requests
import json

# 测试千问VL模型
print("=" * 50)
print("测试千问VL模型")
print("=" * 50)

url = "http://localhost:8000/chat"
headers = {
    "Content-Type": "application/json"
}

test_data_qwen = {
    "prompt": "你好，请简单介绍一下自己。",
    "model": "qwen-vl-plus"
}

try:
    response = requests.post(url, headers=headers, json=test_data_qwen)
    print(f"千问VL - 状态码: {response.status_code}")
    print(f"千问VL - 响应: {response.text[:500]}...")
except Exception as e:
    print(f"千问VL - 错误: {e}")

# 测试DeepSeek模型
print("\n" + "=" * 50)
print("测试DeepSeek模型")
print("=" * 50)

test_data_deepseek = {
    "prompt": "你好，请简单介绍一下自己。",
    "model": "deepseek-chat"
}

try:
    response = requests.post(url, headers=headers, json=test_data_deepseek)
    print(f"DeepSeek - 状态码: {response.status_code}")
    print(f"DeepSeek - 响应: {response.text[:500]}...")
except Exception as e:
    print(f"DeepSeek - 错误: {e}")

print("\n" + "=" * 50)
print("测试完成")
print("=" * 50)