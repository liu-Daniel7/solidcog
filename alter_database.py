import sqlite3
import os

# 获取数据库路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "database.db")

print(f"数据库路径: {DATABASE_PATH}")

# 连接数据库
conn = sqlite3.connect(DATABASE_PATH)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# 执行 ALTER TABLE 命令
try:
    cursor.execute("ALTER TABLE drawings ADD COLUMN layout TEXT;")
    conn.commit()
    print("成功添加 layout 列")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e):
        print("layout 列已存在")
    else:
        print(f"错误: {e}")
finally:
    conn.close()

print("操作完成")