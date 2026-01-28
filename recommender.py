from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
from collections import Counter
import random 

# 1. KẾT NỐI DB
client = MongoClient('mongodb://localhost:27017/')
db = client['refy_shop']
products_collection = db['products']
interactions_collection = db['interactions']
users_collection = db['users']

# 2. HÀM GHI NHẬN TƯƠNG TÁC 
def track_and_learn(user_id, product_id, action="view"):
    if user_id == 'guest_user': return

    if isinstance(product_id, str):
        product_id = ObjectId(product_id)

    # A. Lưu log
    interactions_collection.insert_one({
        "user_id": user_id,
        "product_id": product_id,
        "action": action,
        "timestamp": datetime.now()
    })

    # B. Học sở thích nếu có sp thêm vào giỏ
    if action == "add_to_cart":
        product = products_collection.find_one({'_id': product_id})
        current_user = users_collection.find_one({'_id': ObjectId(user_id)})
        
        if product and current_user:
            current_prefs = current_user.get('preferences', {})
            saved_brands = current_prefs.get('brands', [])
            saved_styles = current_prefs.get('styles', [])

            # Lấy Brand từ attributes
            brand = product.get('attributes', {}).get('brand')
            if brand and brand not in saved_brands:
                saved_brands.insert(0, brand)
            
            # Lấy Style từ Tags / Category
            cat = product.get('category_name')
            if cat and cat not in saved_styles:
                saved_styles.insert(0, cat)
            
            users_collection.update_one(
                {'_id': ObjectId(user_id)},
                {'$set': {
                    'preferences.brands': saved_brands[:5],
                    'preferences.styles': saved_styles[:5]
                }}
            )

# 3. HÀM GỢI Ý THÔNG MINH
def get_recommendations(current_product=None, user_id=None, limit=8):
    """
    Hàm đa năng:
    - Nếu có current_product -> Gợi ý sản phẩm liên quan (Related Products).
    - Nếu KHÔNG có current_product -> Gợi ý theo sở thích Onboarding (Personal Feed).
    """
    recommendation_list = []
    
    # === TRƯỜNG HỢP A: ĐANG XEM 1 SẢN PHẨM (RELATED PRODUCTS) ===
    if current_product:
        current_product_id = current_product['_id']

        # 1. Collaborative Filtering (people also viewed)
        viewers = interactions_collection.find({"product_id": current_product_id}, {"user_id": 1})
        viewer_ids = [v['user_id'] for v in viewers]

        if viewer_ids:
            related_views = interactions_collection.find({
                "user_id": {"$in": viewer_ids},
                "product_id": {"$ne": current_product_id}
            }, {"product_id": 1})
            
            related_product_ids = [r['product_id'] for r in related_views]
            most_common = Counter(related_product_ids).most_common(4)
            
            for pid, count in most_common:
                p = products_collection.find_one({"_id": pid})
                if p:
                    p['reason'] = "People also viewed"
                    recommendation_list.append(p)

        # 2. Content-Based (Similar Products)
        needed = limit - len(recommendation_list)
        if needed > 0:
            existing_ids = [p['_id'] for p in recommendation_list]
            existing_ids.append(current_product_id)
            
            query = { "_id": {"$nin": existing_ids} }
            
            # Ưu tiên tìm cùng Brand hoặc Category
            p_brand = current_product.get('attributes', {}).get('brand')
            p_cat = current_product.get('category_name')
            
            or_conditions = []
            if p_brand: or_conditions.append({"attributes.brand": p_brand})
            if p_cat: or_conditions.append({"category_name": p_cat})
            
            if or_conditions:
                query["$or"] = or_conditions

            content_results = list(products_collection.find(query).limit(needed))
            for p in content_results:
                p['reason'] = "Similar product"
                recommendation_list.append(p)
    
    # === TRƯỜNG HỢP B: TRANG CHỦ (PERSONALIZED FEED - ONBOARDING) ===
    elif user_id:
        user = users_collection.find_one({'_id': ObjectId(user_id)})
        prefs = user.get('preferences', {}) if user else {}
        
        query = {}
        
        # 1. Lọc theo Giới tính (attributes.gender)
        user_gender = prefs.get('gender')
        if user_gender and user_gender != 'unisex':
            # Nếu user chọn Men -> Lấy Men + Unisex
            query['attributes.gender'] = {'$in': [user_gender, 'unisex']}
        
        # 2. Lọc theo Styles (Tìm trong Tags hoặc Category)
        # Lưu ý: Tags trong DB là chữ thường, Onboarding gửi chữ Hoa -> Cần lower()
        user_styles = prefs.get('styles', [])
        if user_styles:
            # Chuyển style người dùng chọn về chữ thường
            styles_lower = [s.lower() for s in user_styles]
            
            query['$or'] = [
                {'tags': {'$in': styles_lower}},       # Khớp Tags
                {'category_name': {'$in': user_styles}}, # Khớp Category
                {'attributes.brand': {'$in': user_styles}} # Khớp Brand
            ]
        
        # 3. Truy vấn DB
        # Dùng aggregate $sample để lấy ngẫu nhiên cho đỡ chán
        pipeline = [
            {'$match': query},
            {'$sample': {'size': limit}}
        ]
        results = list(products_collection.aggregate(pipeline))
        
        for p in results:
            p['reason'] = "Based on your style"
            recommendation_list.append(p)
            
        # 4. Nếu không tìm thấy gì (Cold Start hoàn toàn) -> Lấy sản phẩm mới nhất
        if not recommendation_list:
            fallback = list(products_collection.find().sort('created_at', -1).limit(limit))
            for p in fallback:
                p['reason'] = "New Arrivals"
                recommendation_list.append(p)
                
    # === TRƯỜNG HỢP C: KHÁCH VÃNG LAI (Mới nhất) ===
    else:
         recommendation_list = list(products_collection.find().sort('created_at', -1).limit(limit))

    return recommendation_list