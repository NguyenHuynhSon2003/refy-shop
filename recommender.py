from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
from collections import Counter
import random
import pandas as pd
import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

# 1. KẾT NỐI DB (Dùng làm fallback nếu không truyền tham số)
client = MongoClient('mongodb://localhost:27017/') 
db = client['refy_shop']
_global_products_col = db['products']
_global_interactions_col = db['interactions']
_global_users_col = db['users']

# 2. HÀM GHI NHẬN TƯƠNG TÁC
def track_and_learn(user_id, product_id, action="view"):
    if user_id == 'guest_user': return

    if isinstance(product_id, str):
        product_id = ObjectId(product_id)

    # A. Lưu log
    _global_interactions_col.insert_one({
        "user_id": user_id,
        "product_id": product_id,
        "action": action,
        "timestamp": datetime.now()
    })

    # B. Học sở thích cơ bản (Brand preference)
    if action == "add_to_cart":
        product = _global_products_col.find_one({'_id': product_id})
        if product:
            p_brand = product.get('attributes', {}).get('brand')
            if p_brand:
                _global_users_col.update_one(
                    {'_id': ObjectId(user_id)},
                    {'$addToSet': {'preferences.brands': p_brand}}
                )

# ==========================================================
# 3. THUẬT TOÁN GỢI Ý (ĐÃ UPDATE)
# ==========================================================

def get_recommendations(current_product=None, user_id=None, limit=8, products_col=None, interactions_col=None):
    """
    Hàm gợi ý đa năng:
    - Nếu có products_col truyền vào thì dùng, không thì dùng biến global.
    - Trả về danh sách sản phẩm có kèm field 'match_score'.
    """
    
    # 0. CHUẨN BÓA COLLECTION (Để tránh lỗi NoneType)
    if products_col is None: products_col = _global_products_col
    if interactions_col is None: interactions_col = _global_interactions_col
    
    recommendation_list = []

    # === TRƯỜNG HỢP A: ĐANG XEM SẢN PHẨM (COLLABORATIVE ĐƠN GIẢN) ===
    # (Dùng cho trang chi tiết sản phẩm)
    if current_product:
        current_id = current_product['_id']
        
        # 1. Tìm người khác cùng xem
        viewers = interactions_col.find({"product_id": current_id}, {"user_id": 1})
        viewer_ids = [v['user_id'] for v in viewers]

        if viewer_ids:
            related_views = interactions_col.find({
                "user_id": {"$in": viewer_ids},
                "product_id": {"$ne": current_id}
            }, {"product_id": 1})
            
            pids = [r['product_id'] for r in related_views]
            most_common = Counter(pids).most_common(limit)
            
            for pid, count in most_common:
                p = products_col.find_one({"_id": pid})
                if p:
                    p['reason'] = "People also viewed"
                    p['match_score'] = 0.0 # Mặc định cho logic này
                    recommendation_list.append(p)
        
        # 2. Nếu thiếu -> Fill bằng sản phẩm cùng Category
        if len(recommendation_list) < limit:
            cat = current_product.get('category_name')
            more = list(products_col.find({
                "category_name": cat, 
                "_id": {"$ne": current_id}
            }).limit(limit - len(recommendation_list)))
            for p in more:
                p['reason'] = "Similar products"
                p['match_score'] = 0.0
                recommendation_list.append(p)
                
        return recommendation_list

    # === TRƯỜNG HỢP B: TRANG CHỦ (PERSONALIZED - HYBRID AI) ===
    elif user_id:
        interactions = list(interactions_col.find({'user_id': user_id}))
        
        liked_product_ids = []
        if interactions:
            for act in interactions:
                pid = act['product_id']
                # Tăng trọng số cực mạnh cho hành động xem để AI "nhớ" ngay lập tức
                weight = 1
                if act['action'] == 'view':
                    weight = 2
                elif act['action'] == 'add_to_wishlist': # Kích hoạt sức mạnh của Wishlist
                    weight = 3
                elif act['action'] == 'add_to_cart':
                    weight = 4
                elif act['action'] == 'purchase':
                    weight = 5
                liked_product_ids.extend([pid] * weight)
        
        # Nếu user mới tinh -> Chạy logic Onboarding (Cold Start)
        if not liked_product_ids:
            return get_cold_start_recommendations(user_id, limit, products_col)

        # --- TÍNH ĐIỂM BẰNG TF-IDF ---
        all_products = list(products_col.find())
        if not all_products: return []
        
        df = pd.DataFrame(all_products)
        
        # [CẬP NHẬT QUAN TRỌNG] "Nấu súp" dữ liệu đậm đặc hơn để AI thông minh hơn
        df['soup'] = df['name'].fillna('') + " " + \
                     df['category_name'].fillna('') + " " + \
                     df['descriptions'].fillna('') + " " + \
                     df['tags'].apply(lambda x: " ".join(x) if isinstance(x, list) else "") + " " + \
                     df['attributes'].apply(lambda x: x.get('brand', '') if isinstance(x, dict) else '')

        tfidf = TfidfVectorizer(stop_words='english')
        try:
            tfidf_matrix = tfidf.fit_transform(df['soup'])
            cosine_sim = linear_kernel(tfidf_matrix, tfidf_matrix)
        except ValueError:
            return get_cold_start_recommendations(user_id, limit, products_col)
        
        product_scores = {} 
        liked_indices = df[df['_id'].isin(liked_product_ids)].index.tolist()
        
        for idx in liked_indices:
            sim_scores = list(enumerate(cosine_sim[idx]))
            for i, score in sim_scores:
                product_scores[i] = product_scores.get(i, 0) + score

        sorted_scores = sorted(product_scores.items(), key=lambda x: x[1], reverse=True)
        seen_ids = set(liked_product_ids) 
        
        for idx, score in sorted_scores:
            p_row = df.iloc[idx]
            p_id = p_row['_id']
            
            # Cho phép gợi ý lại những sản phẩm đã xem (vì khách hàng thường xem đi xem lại trước khi mua)
            if p_id not in seen_ids or score > 0.5: 
                p_data = p_row.to_dict()
                p_data['_id'] = p_id 
                
                final_score = score if score < 1.0 else 0.99
                p_data['match_score'] = final_score
                p_data['reason'] = f"Matches {int(final_score*100)}% of your interests"
                
                if not any(r['_id'] == p_id for r in recommendation_list) and p_id not in seen_ids:
                    recommendation_list.append(p_data)
                
            if len(recommendation_list) >= limit:
                break
                
        return recommendation_list

    # === TRƯỜNG HỢP C: KHÁCH VÃNG LAI ===
    return list(products_col.find().sort('created_at', -1).limit(limit))

# --- CẬP NHẬT LOGIC ONBOARDING CHUẨN XÁC ---
def get_cold_start_recommendations(user_id, limit, products_col):
    user = _global_users_col.find_one({'_id': ObjectId(user_id)})
    prefs = user.get('preferences', {}) if user else {}
    
    query = {}
    
    # Sửa lỗi map sai dữ liệu form: Dùng 'categories' và 'gender' thay vì 'styles'
    categories = prefs.get('categories', [])
    gender = prefs.get('gender')
    
    if categories:
        query['category_name'] = {'$in': categories}
        
    if gender:
        # Nếu chọn Men/Women thì lấy thêm cả hàng Unisex
        query['attributes.gender'] = {'$in': [gender, 'unisex', 'Unisex', 'All']} 
    
    pipeline = [{'$match': query}, {'$sample': {'size': limit}}]
    results = list(products_col.aggregate(pipeline))
    
    if not results:
        results = list(products_col.find().sort('created_at', -1).limit(limit))
        
    for p in results: 
        p['reason'] = "Curated from your style profile"
        p['match_score'] = 0.85 
        
    return results


# ==========================================================
# 4. THUẬT TOÁN SVD (COLLABORATIVE FILTERING) - MATRIX FACTORIZATION
# ==========================================================

def get_svd_recommendations(user_id, reviews_col, products_col, n_recommendations=8):
    """
    Gợi ý sản phẩm dựa trên Matrix Factorization (SVD).
    Dành cho User cũ đã từng đánh giá sản phẩm (Rating).
    """
    # 1. Lấy dữ liệu rating từ MongoDB
    # Chỉ lấy các trường cần thiết để tiết kiệm RAM
    reviews = list(reviews_col.find({}, {'user_id': 1, 'product_id': 1, 'rating': 1}))
    
    if not reviews:
        return [] # Chưa có dữ liệu rating nào

    # 2. Tạo DataFrame
    df = pd.DataFrame(reviews)
    
    # Chuyển đổi ID sang string để đảm bảo tính nhất quán
    df['user_id'] = df['user_id'].astype(str)
    df['product_id'] = df['product_id'].astype(str)

    # 3. Tạo Ma trận Utility (User x Product)
    # Hàng = User, Cột = Product, Giá trị = Rating
    # fill_value=0 nghĩa là chưa đánh giá thì coi như 0 điểm
    try:
        ratings_matrix = df.pivot_table(values='rating', index='user_id', columns='product_id', fill_value=0)
    except Exception as e:
        print(f"Error creating pivot table: {e}")
        return []

    # Kiểm tra xem user hiện tại có trong ma trận không (Cold Start cho SVD)
    if str(user_id) not in ratings_matrix.index:
        return [] # User này chưa từng rating -> Trả về rỗng để dùng TF-IDF fallback

    # 4. Áp dụng thuật toán SVD (Ma trận hóa)
    # Transpose ma trận để tính tương đồng giữa các Items (Item-based SVD)
    X = ratings_matrix.values.T 
    
    # Chọn số chiều không gian ẩn (Latent Factors). 
    # Với dữ liệu nhỏ (<1000 sp), chọn 10-12 là đẹp.
    n_components = min(12, X.shape[1] - 1) 
    if n_components < 2: return [] # Quá ít dữ liệu để chạy

    SVD = TruncatedSVD(n_components=n_components, random_state=42)
    SVD_matrix = SVD.fit_transform(X)

    # 5. Tính ma trận tương quan (Correlation Matrix)
    # Kết quả: Mức độ giống nhau giữa các sản phẩm dựa trên hành vi chấm điểm của User
    corr_mat = np.corrcoef(SVD_matrix)

    # 6. Dự đoán gợi ý
    # Lấy danh sách các sản phẩm User này đã đánh giá CAO (>= 4 sao)
    user_ratings = ratings_matrix.loc[str(user_id)]
    liked_products = user_ratings[user_ratings >= 4].index.tolist()

    product_ids_in_matrix = ratings_matrix.columns.tolist()
    similar_products = []

    for product_id in liked_products:
        if product_id in product_ids_in_matrix:
            idx = product_ids_in_matrix.index(product_id)
            
            # Lấy dòng tương quan của sản phẩm này với tất cả sp khác
            correlation_scores = corr_mat[idx]
            
            # Lấy Top các sản phẩm tương quan nhất (trừ chính nó)
            recommend_idxs = correlation_scores.argsort()[-(n_recommendations+1):][::-1]
            
            for rec_idx in recommend_idxs:
                rec_pid = product_ids_in_matrix[rec_idx]
                if rec_pid != product_id:
                    similar_products.append(rec_pid)

    # 7. Lọc trùng và lấy thông tin chi tiết từ DB
    unique_pids = list(set(similar_products))[:n_recommendations]
    
    recommended_items = []
    for pid in unique_pids:
        try:
            # Tìm trong DB để lấy tên, giá, ảnh...
            prod = products_col.find_one({'_id': ObjectId(pid)})
            if prod:
                prod['match_score'] = 0.98 # SVD thường rất chính xác nên cho điểm cao
                prod['reason'] = "Based on your rating history" # Lý do gợi ý
                recommended_items.append(prod)
        except:
            pass

    return recommended_items