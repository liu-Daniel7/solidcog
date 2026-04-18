import sqlite3
import os

# 获取数据库路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "database.db")

print("检查数据库状态...")
print(f"数据库文件路径: {DATABASE_PATH}")
print(f"数据库文件存在: {os.path.exists(DATABASE_PATH)}")

if os.path.exists(DATABASE_PATH):
    # 连接数据库
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # 检查表结构
    print("\n检查表结构:")
    cursor.execute("PRAGMA table_info(drawings)")
    columns = cursor.fetchall()
    for column in columns:
        print(f"列: {column[1]}, 类型: {column[2]}")
    
    # 检查数据
    print("\n检查数据:")
    cursor.execute("SELECT COUNT(*) FROM drawings")
    count = cursor.fetchone()[0]
    print(f"图纸数量: {count}")
    
    if count > 0:
        print("\n最近的5条记录:")
        cursor.execute("SELECT id, filename, upload_time FROM drawings ORDER BY upload_time DESC LIMIT 5")
        rows = cursor.fetchall()
        for row in rows:
            print(f"ID: {row[0]}, 文件名: {row[1]}, 上传时间: {row[2]}")
    
    conn.close()
else:
    print("数据库文件不存在，需要初始化")

print("\n检查完成。")