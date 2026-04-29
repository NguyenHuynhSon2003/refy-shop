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

# 1. Connect to MongoDB
client = MongoClient('mongodb://localhost:27017/') 
db = client['refy_shop']
_global_products_col = db['products']
_global_interactions_col = db['interactions']
_global_users_col = db['users']

# --- (WEIGHTED INTERACTION) ---
ACTION_MULTIPLIERS = {
    'view': 0.65,          
    'add_to_wishlist': 0.85,      
    'add_to_cart': 0.95,   
    'purchase': 1.0,
    'rating': 1.0          
}
ACTION_PRIORITY = {'view': 1, 'add_to_wishlist': 2, 'add_to_cart': 3, 'purchase': 4, 'rating': 5}

# 2. INTERACTION RECORDING FUNCTION
def track_and_learn(user_id, product_id, action="view"):
    if user_id == 'guest_user': return

    if isinstance(product_id, str):
        product_id = ObjectId(product_id)

    # A. Save log
    _global_interactions_col.insert_one({
        "user_id": user_id,
        "product_id": product_id,
        "action": action,
        "timestamp": datetime.now()
    })

    # B. (Brand preference)
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
# 3. RECOMMENDATION ALGORITHM
# ==========================================================

def get_recommendations(current_product=None, user_id=None, limit=8, products_col=None, interactions_col=None):
    
    if products_col is None: products_col = _global_products_col
    if interactions_col is None: interactions_col = _global_interactions_col
    recommendation_list = []
    # === CASE A: VIEWING THE PRODUCT (SIMPLE COLLABORATIVE) ===
    if current_product:
        current_id = current_product['_id']
        
        # 1. Find other users who viewed the same product
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
                    p['match_score'] = 0.0
                    recommendation_list.append(p)
        # 2. If missing -> Fill with products from the same category
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

    # === CASE B: HOME PAGE (PERSONALIZED - HYBRID AI) ===
    elif user_id:
        interactions = list(interactions_col.find({'user_id': user_id}))
        
        liked_product_ids = []
        interacted_products = {} # Save the strongest action for each product (view < wishlist < cart < purchase)
        
        if interactions:
            for act in interactions:
                pid = act['product_id']
                action_type = act.get('action', 'view')
                
                liked_product_ids.append(pid)
                
                str_pid = str(pid)
                if str_pid not in interacted_products:
                    interacted_products[str_pid] = action_type
                else:
                    current_act = interacted_products[str_pid]
                    if ACTION_PRIORITY.get(action_type, 0) > ACTION_PRIORITY.get(current_act, 0):
                        interacted_products[str_pid] = action_type
        
        # If user has no interactions, fallback to cold start recommendations
        if not liked_product_ids:
            return get_cold_start_recommendations(user_id, limit, products_col)

        # --- COMPUTE SIMILARITY SCORES ---
        all_products = list(products_col.find())
        if not all_products: return []
        
        df = pd.DataFrame(all_products)
        
        # create 'soup' for TF-IDF: combine name, category, description, tags, and brand into one text field
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
            # Get the original product ID to see what the user has done with it.
            source_pid = str(df.iloc[idx]['_id'])
            source_action = interacted_products.get(source_pid, 'view')
            multiplier = ACTION_MULTIPLIERS.get(source_action, 0.65)
            
            sim_scores = list(enumerate(cosine_sim[idx]))
            for i, score in sim_scores:
                # Multiply similarity score by the action multiplier to give more weight to stronger interactions
                weighted_score = score * multiplier
                product_scores[i] = max(product_scores.get(i, 0), weighted_score)

        # Sort all scores from high to low
        sorted_scores = sorted(product_scores.items(), key=lambda x: x[1], reverse=True)
        
        # --- IMPLEMENTATION OF SERENDIPITY MECHANISM ---
        # 1. Take mostly high-scoring items (Keep the limit - 2 slots)
        top_matches = sorted_scores[:limit - 2]
        # 2. Filter out a basket of products with very low scores (10%).
        low_matches = [item for item in sorted_scores if item[1] < 0.10]
        # 3. Randomly select 2 items from this low-score basket to add an element of surprise (Serendipity)
        discovery_items_raw = random.sample(low_matches, min(2, len(low_matches)))
        discovery_items = []
        for idx, old_score in discovery_items_raw:
            fake_score = random.uniform(0.15, 0.35)
            discovery_items.append((idx, fake_score))
        # 4. Gộp danh sách lại thành kết quả cuối cùng
        final_scores = top_matches + discovery_items
        seen_ids = set(liked_product_ids) 
        # Iterate through final_scores instead of sorted_scores.
        for idx, score in final_scores:
            p_row = df.iloc[idx]
            p_id = p_row['_id']
            # Allow for suggestions to revisit products that have already been viewed
            if p_id not in seen_ids or score > 0.5: 
                p_data = p_row.to_dict()
                p_data['_id'] = p_id 
                final_score = score if score < 1.0 else 0.99
                p_data['match_score'] = final_score
                p_data['reason'] = f"Matches {int(final_score*100)}% of your interests"
                if not any(r['_id'] == p_id for r in recommendation_list):
                    recommendation_list.append(p_data)
            if len(recommendation_list) >= limit:
                break
        return recommendation_list

    # === CASE 4: Cold Start Recommendations ===
    return list(products_col.find().sort('created_at', -1).limit(limit))

# --- UPDATE ONBOARDING LOGIC ACCURATELY ---
def get_cold_start_recommendations(user_id, limit, products_col):
    user = _global_users_col.find_one({'_id': ObjectId(user_id)})
    prefs = user.get('preferences', {}) if user else {}
    
    query = {}
    
    # Filter by categories and gender if available in user preferences
    categories = prefs.get('categories', [])
    gender = prefs.get('gender')
    
    if categories:
        query['category_name'] = {'$in': categories}
        
    if gender:
        # If user has a gender preference, include products that match that gender or are unisex
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
# 4. SVD-BASED RECOMMENDATION FOR RATING USERS (COLLABORATIVE FILTERING)
# ==========================================================

def get_svd_recommendations(user_id, reviews_col, products_col, n_recommendations=8):
    # 1. Get rating data from MongoDB.
    reviews = list(reviews_col.find({}, {'user_id': 1, 'product_id': 1, 'rating': 1}))
    
    if not reviews:
        return []

    # 2. Create DataFrame
    df = pd.DataFrame(reviews)
    
    # Ensure user_id and product_id are strings for pivoting
    df['user_id'] = df['user_id'].astype(str)
    df['product_id'] = df['product_id'].astype(str)

    # 3. Create Utility Matrix (User x Product)
    # Row = User, Column = Product, Value = Rating
    # fill_value=0 means unrated items are considered as 0 rating
    try:
        ratings_matrix = df.pivot_table(values='rating', index='user_id', columns='product_id', fill_value=0)
    except Exception as e:
        print(f"Error creating pivot table: {e}")
        return []

    # Check if the user exists in the ratings matrix
    if str(user_id) not in ratings_matrix.index:
        return [] 

    # 4. Apply SVD algorithm (Matrix Factorization)
    X = ratings_matrix.values.T 
    
    # Choose the number of latent factors.
    # For small datasets (<1000 items), choosing 10-12 is appropriate.
    n_components = min(12, X.shape[1] - 1) 
    if n_components < 2: return [] # Quá ít dữ liệu để chạy

    SVD = TruncatedSVD(n_components=n_components, random_state=42)
    SVD_matrix = SVD.fit_transform(X)

    # 5. Correlation Matrix
    corr_mat = np.corrcoef(SVD_matrix)

    # 6. Prediction suggestions
    user_ratings = ratings_matrix.loc[str(user_id)]
    liked_products = user_ratings[user_ratings >= 4].index.tolist()

    product_ids_in_matrix = ratings_matrix.columns.tolist()
    similar_products = []

    for product_id in liked_products:
        if product_id in product_ids_in_matrix:
            idx = product_ids_in_matrix.index(product_id)
            

            correlation_scores = corr_mat[idx]
            
            # Get the top most relevant products (excluding itself).
            recommend_idxs = correlation_scores.argsort()[-(n_recommendations+1):][::-1]
            
            for rec_idx in recommend_idxs:
                rec_pid = product_ids_in_matrix[rec_idx]
                if rec_pid != product_id:
                    similar_products.append(rec_pid)

    # 7. Remove duplicates and limit to n_recommendations
    unique_pids = list(set(similar_products))[:n_recommendations]
    
    recommended_items = []
    for pid in unique_pids:
        try:
            # Fetch product details from MongoDB to return to the user.
            prod = products_col.find_one({'_id': ObjectId(pid)})
            if prod:
                prod['match_score'] = 0.98 # SVD usually very accurate so give high score
                prod['reason'] = "Based on your rating history" # Reason for recommendation
                recommended_items.append(prod)
        except:
            pass

    return recommended_items