import requests
import json

# 测试 DeepSeek API
url = "http://localhost:8000/chat"

# 测试数据
test_data = {
    "prompt": "你好，DeepSeek！",
    "model": "deepseek-chat"
}

# 发送请求
response = requests.post(url, json=test_data)

print("响应状态码:", response.status_code)
print("响应内容:", response.text)