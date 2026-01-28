from pymongo import MongoClient
from datetime import datetime

# Kết nối Database
client = MongoClient('mongodb://localhost:27017/')
db = client['refy_shop']
categories_collection = db['categories']

# Cập nhật tất cả danh mục KHÔNG có trường created_at
# Đặt ngày mặc định là hiện tại
result = categories_collection.update_many(
    {'created_at': {'$exists': False}},  # Điều kiện: Chưa có created_at
    {'$set': {'created_at': datetime.now()}} # Hành động: Thêm ngày hiện tại
)

print(f"Đã sửa xong! Cập nhật {result.modified_count} danh mục cũ.")