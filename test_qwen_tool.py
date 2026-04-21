import requests
import json

BASE_URL = "http://localhost:8000"

print("=" * 60)
print("测试千问VL工具调用功能")
print("=" * 60)

# 测试1：查询数据库
def test_query_database():
    print("\n测试1：查询数据库")
    print("-" * 40)
    
    # 测试查询图纸列表
    payload = {
        "tool_call": "query_database",
        "parameters": {
            "query_type": "list",
            "limit": 5
        }
    }
    
    response = requests.post(f"{BASE_URL}/qwen-tool", json=payload)
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.json()}")
    
    # 测试查询图纸总数
    payload = {
        "tool_call": "query_database",
        "parameters": {
            "query_type": "count"
        }
    }
    
    response = requests.post(f"{BASE_URL}/qwen-tool", json=payload)
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.json()}")

# 测试2：搜索图纸
def test_search_drawings():
    print("\n测试2：搜索图纸")
    print("-" * 40)
    
    payload = {
        "tool_call": "search_drawings",
        "parameters": {
            "keyword": "技术",
            "limit": 5
        }
    }
    
    response = requests.post(f"{BASE_URL}/qwen-tool", json=payload)
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.json()}")

# 测试3：分析图纸
def test_analyze_drawing():
    print("\n测试3：分析图纸")
    print("-" * 40)
    
    # 使用测试图片
    test_image = "debug_vertical_tech.png"
    
    payload = {
        "tool_call": "analyze_drawing",
        "parameters": {
            "image_path": test_image,
            "model_type": "plus"
        }
    }
    
    response = requests.post(f"{BASE_URL}/qwen-tool", json=payload)
    print(f"状态码: {response.status_code}")
    result = response.json()
    print(f"响应: {result}")
    
    if result.get("success"):
        data = result.get("data", {})
        print(f"标题栏长度: {len(data.get('title_block', ''))}")
        print(f"技术要求长度: {len(data.get('tech_block', ''))}")
        print(f"完整结果长度: {len(data.get('all_text', ''))}")

if __name__ == "__main__":
    test_query_database()
    test_search_drawings()
    test_analyze_drawing()
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)