from pymongo import MongoClient
from datetime import datetime
from werkzeug.security import generate_password_hash # Dùng để mã hóa mật khẩu chuẩn bảo mật
import random

# --- KẾT NỐI MONGODB ---
client = MongoClient('mongodb://localhost:27017/')
db = client['refy_shop']

def init_db_standard():
    print("⏳ Đang xóa dữ liệu cũ...")
    # Xóa sạch các collection cũ để tránh xung đột
    db.users.drop()
    db.products.drop()
    db.categories.drop()
    db.carts.drop()
    db.orders.drop()
    db.user_interactions.drop() # Tên mới theo ERD
    db.reviews.drop()

    print("🚀 Đang khởi tạo dữ liệu theo chuẩn ERD Mentor...")

    # --- 1. TẠO CATEGORIES (Bảng Category trong ERD) ---
    # ERD: id, name, description
    categories_data = [
        {"name": "Sneakers", "description": "High quality sneakers for sport and casual"},
        {"name": "Boots", "description": "Durable boots for all terrains"},
        {"name": "Sandals", "description": "Comfortable slides and sandals"},
        {"name": "Formal", "description": "Elegant shoes for office and parties"}
    ]
    # Lưu vào DB và lấy lại ID để gán cho sản phẩm (Foreign Key giả lập)
    cat_ids = {} 
    for cat in categories_data:
        res = db.categories.insert_one(cat)
        cat_ids[cat['name']] = res.inserted_id

    # --- 2. TẠO PRODUCTS (Bảng Product trong ERD) ---
    # ERD: category_id (FK), name, price, stock, descriptions, image, attributes, tags, create_date
    
    # Dữ liệu mẫu
    base_products = [
        {"name": "Nike Air Force 1", "price": 2900000, "cat": "Sneakers", "brand": "Nike"},
        {"name": "Adidas Stan Smith", "price": 2500000, "cat": "Sneakers", "brand": "Adidas"},
        {"name": "Timberland Boot", "price": 5200000, "cat": "Boots", "brand": "Timberland"},
        {"name": "Converse Chuck 70", "price": 1900000, "cat": "Sneakers", "brand": "Converse"},
        {"name": "Nike Jordan 1", "price": 4500000, "cat": "Sneakers", "brand": "Nike"}
    ]

    image_sets = {
        "nike": ["https://images.unsplash.com/photo-1600185365926-3a2ce3cdb9eb?q=80&w=800", "https://images.unsplash.com/photo-1542291026-7eec264c27ff?q=80&w=800"],
        "adidas": ["https://images.unsplash.com/photo-1587563871167-1ee9c731aefb?q=80&w=800", "https://images.unsplash.com/photo-1550399563-356195531d22?q=80&w=800"],
        "boots": ["https://images.unsplash.com/photo-1608256246200-53e635b5b69f?q=80&w=800", "https://images.unsplash.com/photo-1605034313761-73ea4a0cfbf3?q=80&w=800"],
        "converse": ["https://images.unsplash.com/photo-1607522370275-f14206abe5d3?q=80&w=800", "https://images.unsplash.com/photo-1494496195158-c31b4306c7ee?q=80&w=800"]
    }

    final_products = []
    for i in range(12): # Tạo 12 sản phẩm
        p = random.choice(base_products)
        
        # Chọn ảnh
        if 'Nike' in p['brand']: imgs = image_sets['nike']
        elif 'Adidas' in p['brand']: imgs = image_sets['adidas']
        elif 'Converse' in p['brand']: imgs = image_sets['converse']
        else: imgs = image_sets['boots']

        product_doc = {
            "name": f"{p['name']} (Vol. {i+1})",
            "category_id": cat_ids[p['cat']], # <--- KHÓA NGOẠI (Foreign Key) trỏ về bảng Category
            "category_name": p['cat'],        # Lưu thêm tên để tiện hiển thị (Denormalization)
            "price": p['price'],
            "stock": random.randint(10, 100),
            "descriptions": f"<p>This is a premium <strong>{p['brand']}</strong> product.</p>",
            "image": imgs[0],                 # Ảnh đại diện
            "images": imgs,                   # Album ảnh
            "attributes": {                   # Trường attributes kiểu JSON như trong ERD
                "brand": p['brand'],
                "gender": random.choice(["men", "women", "unisex"]),
                "material": "Leather"
            },
            "tags": [p['cat'].lower(), p['brand'].lower(), "trending"],
            "create_date": datetime.now()
        }
        final_products.append(product_doc)
    
    db.products.insert_many(final_products)

    # --- 3. TẠO USERS (Bảng User trong ERD) ---
    # ERD: email, password, full_name, billing_address, default_shipping_address, phone, role
    
    # User 1: ADMIN
    admin_user = {
        "email": "admin@refy.com",
        "password": generate_password_hash("123"), # <--- MÃ HÓA MẬT KHẨU (Bảo mật hơn '123' thường)
        "full_name": "Son Nguyen Admin",
        "phone": "0909000111",
        "role": "admin", # Phân quyền
        "billing_address": "Can Tho City, Vietnam",
        "default_shipping_address": "Ninh Kieu, Can Tho",
        "created_at": datetime.now()
    }

    # User 2: CUSTOMER
    customer_user = {
        "email": "khachhang@gmail.com",
        "password": generate_password_hash("123"), 
        "full_name": "Nguyen Van A",
        "phone": "0933444555",
        "role": "customer",
        "billing_address": None,
        "default_shipping_address": "District 1, HCMC",
        "created_at": datetime.now()
    }

    db.users.insert_many([admin_user, customer_user])

    print("✅ Đã tạo xong Database chuẩn ERD!")
    print("👉 Admin: admin@refy.com / 123")
    print("👉 Customer: khachhang@gmail.com / 123")

if __name__ == '__main__':
    init_db_standard()