import sqlite3

# 连接数据库
conn = sqlite3.connect('database.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# 查询所有图纸信息
cursor.execute('SELECT id, filename, file_size, upload_time, ocr_text FROM drawings ORDER BY upload_time ASC')
rows = cursor.fetchall()

print(f"\n数据库中共有 {len(rows)} 个图纸记录\n")

for row in rows:
    print(f"编号: {row['id']}")
    print(f"文件名: {row['filename']}")
    print(f"文件大小: {row['file_size']} 字节")
    print(f"上传时间: {row['upload_time']}")
    print(f"OCR 内容长度: {len(row['ocr_text']) if row['ocr_text'] else 0} 字符")
    if row['ocr_text']:
        print("OCR 内容预览:")
        preview = row['ocr_text'][:200]  # 只显示前 200 个字符
        print(preview + ("..." if len(row['ocr_text']) > 200 else ""))
    else:
        print("OCR 内容: 无")
    print("-" * 50)

# 关闭连接
conn.close()