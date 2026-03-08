import random
from pymongo import MongoClient

# Kết nối đến Database của bạn (Nhớ đổi tên 'refy_shop' nếu tên DB của bạn khác)
client = MongoClient('mongodb://localhost:27017/')
db = client['refy_shop'] 
products_col = db['products']

# Các size giày chuẩn
shoe_sizes = ["38", "39", "40", "41", "42"]

# Lấy tất cả sản phẩm
products = products_col.find({})
count = 0

for p in products:
    sizes_stock = []
    
    # Tạo ngẫu nhiên số lượng tồn kho cho từng size (từ 5 đến 20 đôi)
    for size in shoe_sizes:
        sizes_stock.append({
            "size": size,
            "quantity": random.randint(5, 20) 
        })
    
    # Cập nhật trường sizes_stock vào sản phẩm
    products_col.update_one(
        {'_id': p['_id']},
        {'$set': {'sizes_stock': sizes_stock}}
    )
    count += 1

print(f"✅ Đã nâng cấp thành công tồn kho theo size cho {count} sản phẩm!")