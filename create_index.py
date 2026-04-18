import sqlite3
import os

# 获取数据库路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "database.db")

# 连接数据库
conn = sqlite3.connect(DATABASE_PATH)
cursor = conn.cursor()

# 创建OCR文本索引
print("创建OCR文本索引...")
try:
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_ocr_text
    ON drawings (ocr_text);
    """)
    conn.commit()
    print("索引创建成功！")
except Exception as e:
    print(f"索引创建失败: {e}")
finally:
    conn.close()

print("操作完成。")