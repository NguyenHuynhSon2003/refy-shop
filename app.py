from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from datetime import datetime
from bson.objectid import ObjectId
from authlib.integrations.flask_client import OAuth
import werkzeug.security
import os
import random


# chạy OAuth trên http (localhost)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
# --- IMPORT MODULE TRÍ TUỆ NHÂN TẠO (FILE VỪA TẠO) ---
from recommender import track_and_learn, get_recommendations 
# -----------------------------------------------------

app = Flask(__name__)

# --- CẤU HÌNH GOOGLE OAUTH ---
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id='408459870213-iljor43eeoh992k27cni9o781nqltgci.apps.googleusercontent.com',
    client_secret='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',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

app.jinja_env.globals.update(str=str)
app.secret_key = 'khoa_bi_mat_cua_du_an'

# --- KẾT NỐI MONGODB ---
mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(mongo_uri)
db = client['refy_shop']
products_collection = db['products']
users_collection = db['users']
interactions_collection = db['interactions']
wishlists_collection = db['wishlists']
categories_collection = db['categories']
orders_collection = db['orders'] # <--- BẢNG ĐƠN HÀNG
reviews_collection = db['reviews']
ADMIN_EMAIL = 'admin@refy.com'

# --- 1. AUTH ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        email = request.form.get('email')
        password = request.form.get('password')

        # --- XỬ LÝ ĐĂNG KÝ ---
        if action == 'register':
            # [MỚI 1] Lấy mật khẩu xác nhận từ form
            confirm_password = request.form.get('confirm_password')

            # [MỚI 2] Kiểm tra mật khẩu có khớp không
            if password != confirm_password:
                flash("The verification password doesn't match!", "error")
                # Trả về trang login nhưng giữ lại email đã nhập cho tiện
                return render_template('login.html', email_entered=email)

            # Kiểm tra Email đã tồn tại chưa (Code cũ)
            if users_collection.find_one({'email': email}):
                flash("Email already exists!", "error")
                return render_template('login.html', email_entered=email)
            
            # [CHUẨN ERD] Tạo User mới
            new_user = {
                'email': email, 
                'password': generate_password_hash(password), # Mã hóa password
                'full_name': email.split('@')[0],             # Mapping vào cột full_name
                'role': 'customer',                           # Mặc định là khách
                'created_at': datetime.now(),
                'is_onboarded': False
            }
            users_collection.insert_one(new_user)
            session['user_id'] = str(new_user['_id'])
            
            flash("Registration successful! Let's personalize your feed.", "success")
            return redirect(url_for('onboarding'))

        # --- XỬ LÝ ĐĂNG NHẬP (Giữ nguyên) ---
        elif action == 'login':
            user = users_collection.find_one({'email': email})
            
            if user and check_password_hash(user['password'], password): 
                session['user_id'] = str(user['_id'])
                
                display_name = user.get('full_name', user.get('name', 'User'))

                if not user.get('is_onboarded'):
                    flash(f"Welcome back, {display_name}! Please complete your profile.", "info")
                    return redirect(url_for('onboarding'))
                
                flash(f"Welcome back, {display_name}!", "success") 
                
                return redirect(url_for('home')) 
            else:
                flash("Invalid email or password!", "error")
                return render_template('login.html', email_entered=email)
                
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out successfully.", "info")
    return redirect(url_for('home'))

# --- 2. HÀM INIT DB


# --- 3. PUBLIC ROUTES ---
@app.route('/')
@app.route('/men')
@app.route('/women')
@app.route('/unisex')
def home():
    current_path = request.path
    products = []
    
    # A. Nếu đang lọc cứng theo Menu (Men/Women) -> Giữ nguyên logic lọc
    if current_path in ['/men', '/women', '/unisex']:
        query = {}
        gender_map = {
            '/men': ['men', 'unisex'],
            '/women': ['women', 'unisex'],
            '/unisex': ['unisex']
        }
        target_genders = gender_map.get(current_path)
        if target_genders:
            query = {"attributes.gender": {"$in": target_genders}}
        
        products = list(products_collection.find(query).sort('created_at', -1))
        
    # B. Nếu ở Trang Chủ ('/') -> Dùng AI Gợi ý (Onboarding)
    else:
        user_id = session.get('user_id')
        # Gọi hàm AI mới, không truyền current_product, chỉ truyền user_id
        products = get_recommendations(current_product=None, user_id=user_id, limit=12)

    # Wishlist logic (Giữ nguyên)
    user_wishlist = []
    if 'user_id' in session:
        wishlist_data = wishlists_collection.find({'user_id': session['user_id']}, {'product_id': 1})
        user_wishlist = [str(item['product_id']) for item in wishlist_data]

    return render_template('index.html', products=products, user_wishlist=user_wishlist)

# --- 4. CHI TIẾT SẢN PHẨM (ĐÃ NÂNG CẤP GỌI AI MODULE) ---

# --- HÀM KIỂM TRA QUYỀN REVIEW  ---
def check_can_review(user_id, product_id):
    # Điều kiện 1: Phải có đơn hàng trạng thái 'delivered' chứa sản phẩm này
    has_bought = orders_collection.find_one({
        'user_id': user_id,               # ID người dùng (String)
        'status': 'delivered',            # Đơn hàng phải đã giao
        'items.product_id': str(product_id) # Sản phẩm có trong đơn
    })

    # Điều kiện 2: Chưa từng review sản phẩm này (Tránh spam)
    already_reviewed = reviews_collection.find_one({
        'user_id': user_id,
        'product_id': ObjectId(product_id)
    })

    # Nếu đã mua VÀ chưa review thì trả về True
    if has_bought and not already_reviewed:
        return True
    return False

@app.route('/product/<product_id>')
def product_detail(product_id):
    # --- PHẦN 1: LẤY THÔNG TIN SẢN PHẨM (Code gốc) ---
    try:
        p_id = ObjectId(product_id)
        product = products_collection.find_one({"_id": p_id})
    except:
        return "Invalid Product ID", 400
        
    if not product: return "Product not found", 404

    current_user = session.get('user_id', 'guest_user')

    # --- PHẦN 2: TÍNH NĂNG AI ---
    # Ghi log hành vi xem hàng
    track_and_learn(current_user, p_id, action="view")
    
    # Lấy danh sách gợi ý (Recommendation)
    recommendations = get_recommendations(product, current_user)

    # --- PHẦN 3: TÍNH NĂNG REVIEW  ---
    # 3.1. Lấy danh sách đánh giá từ DB (Mới nhất lên đầu)
    reviews = list(reviews_collection.find({'product_id': p_id}).sort('created_at', -1))
    
    # 3.2. Kiểm tra quyền Review (Chỉ user đã mua & nhận hàng mới được True)
    can_review = False
    if 'user_id' in session:
        # Gọi hàm check_can_review đã viết ở các bước trước
        can_review = check_can_review(session['user_id'], product_id)

    # --- RETURN TEMPLATE ---
    return render_template('product_detail.html', 
                           product=product, 
                           recommendations=recommendations, # Dữ liệu AI
                           reviews=reviews,                 # Dữ liệu Review
                           can_review=can_review)           # Biến kiểm tra quyền

# --- ROUTE XỬ LÝ GỬI REVIEW (Dán vào app.py) ---
@app.route('/submit-review/<product_id>', methods=['POST'])
def submit_review(product_id):
    # 1. Kiểm tra đăng nhập
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # 2. Lấy dữ liệu từ form HTML
    rating = request.form.get('rating')
    comment = request.form.get('comment')
    
    # 3. Lưu vào Database
    # Lưu ý: Chuyển rating thành số nguyên (int)
    reviews_collection.insert_one({
        'user_id': session['user_id'],
        'user_name': session.get('name', 'Customer'), # Lấy tên từ session
        'product_id': ObjectId(product_id),
        'rating': int(rating),
        'comment': comment,
        'created_at': datetime.now()
    })

    flash("Thanks for your review!", "success")

    # 4. Quay lại trang chi tiết sản phẩm
    return redirect(url_for('product_detail', product_id=product_id))


# --- 5. CART ROUTES ---
@app.route('/add-to-cart/<product_id>', methods=['POST'])
def add_to_cart(product_id):
    if 'user_id' not in session: 
        # Tiếng Anh: Login required
        flash("Please login to add items to your cart!", "warning")
        return redirect(url_for('login'))

    user_id = session['user_id']
    p_id = ObjectId(product_id)
    quantity = int(request.form.get('quantity', 1))
    size = request.form.get('size')
    
    product = products_collection.find_one({'_id': p_id})
    main_image = product['images'][0] if product.get('images') else product.get('image', '')
    
    db['carts'].update_one(
        {'user_id': user_id},
        {'$push': {'items': {
            'product_id': p_id, 
            'name': product['name'], 
            'price': product['price'], 
            'image': main_image,
            'size': size, 
            'quantity': quantity
        }}},
        upsert=True
    )

    # Tracking AI
    track_and_learn(user_id, p_id, action="add_to_cart")

    # Tiếng Anh: Success
    flash("Product added to cart successfully!", "success") 
    return redirect(url_for('view_cart'))

@app.route('/cart')
def view_cart():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_cart = db['carts'].find_one({'user_id': session['user_id']})
    items = user_cart.get('items', []) if user_cart else []
    total_price = sum(item['price'] * item['quantity'] for item in items)
    return render_template('cart.html', items=items, total=total_price)

@app.route('/remove-cart/<product_id>', methods=['POST'])
def remove_from_cart(product_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    size_to_remove = request.form.get('size')
    db['carts'].update_one(
        {'user_id': session['user_id']},
        {'$pull': {'items': {'product_id': ObjectId(product_id), 'size': size_to_remove}}}
    )
    flash("Item removed from your cart.", "info")
    return redirect(url_for('view_cart'))


# --- 6. WISHLIST & COLLECTIONS (GIỮ NGUYÊN) ---
@app.route('/wishlist')
# def wishlist():
#     if 'user_id' not in session: 
#         flash("Please log in to view your favorites list!", "warning")
#         return redirect(url_for('login'))
#     wishlist_items = list(wishlists_collection.find({'user_id': session['user_id']}))
#     product_ids = [item['product_id'] for item in wishlist_items]
#     products = list(products_collection.find({'_id': {'$in': product_ids}}))
#     return render_template('wishlist.html', products=products)
def wishlist():
    products = [] # Mặc định danh sách rỗng
    
    # Chỉ lấy dữ liệu khi ĐÃ ĐĂNG NHẬP
    if 'user_id' in session:
        wishlist_items = list(wishlists_collection.find({'user_id': session['user_id']}))
        product_ids = [item['product_id'] for item in wishlist_items]
        # Nếu có sản phẩm trong wishlist thì mới query
        if product_ids:
            products = list(products_collection.find({'_id': {'$in': product_ids}}))
    
    # Luôn hiển thị trang wishlist.html dù chưa đăng nhập
    return render_template('wishlist.html', products=products)

@app.route('/api/toggle_wishlist/<product_id>', methods=['POST'])
def toggle_wishlist(product_id):
    if 'user_id' not in session: return jsonify({'status': 'error', 'message': 'Login required'}), 401
    user_id = session['user_id']
    p_id = ObjectId(product_id)
    if wishlists_collection.find_one({'user_id': user_id, 'product_id': p_id}):
        wishlists_collection.delete_one({'user_id': user_id, 'product_id': p_id})
        return jsonify({'status': 'removed'})
    else:
        wishlists_collection.insert_one({'user_id': user_id, 'product_id': p_id, 'timestamp': datetime.now()})
        return jsonify({'status': 'added'})

@app.route('/collection/<collection_name>')
def collection(collection_name):
    page_title = collection_name.replace('-', ' ').title()
    products = []
    if collection_name == 'new-drops': products = list(products_collection.find().sort('_id', -1).limit(8))
    elif collection_name == 'sale': products = list(products_collection.find({'price': {'$lt': 1500000}}))
    elif collection_name == 'sneakers': products = list(products_collection.find({'category': 'Sneakers'}))
    elif collection_name == 'running': products = list(products_collection.find({'$or': [{'name': {'$regex': 'Running', '$options': 'i'}}, {'tags': 'running'}]}))
    elif collection_name == 'basketball': products = list(products_collection.find({'$or': [{'name': {'$regex': 'Jordan', '$options': 'i'}}, {'tags': 'basketball'}]}))
    elif collection_name == 'slides-sandals': products = list(products_collection.find({'category': 'Sandals'}))
    return render_template('index.html', products=products, gender=collection_name, page_title=page_title)

# --- 7. ADMIN ROUTES ---
def is_admin():
    if 'user_id' not in session: return False
    user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    # Kiểm tra theo field 'role' trong ERD hoặc email admin cứng
    return user and (user.get('role') == 'admin' or user.get('email') == ADMIN_EMAIL)

@app.route('/admin')
def admin_dashboard():
    if not is_admin(): 
        flash(f"Access Denied", "warning")
        return redirect(url_for('home'))
    stats = {
        'total_products': products_collection.count_documents({}),
        'total_users': users_collection.count_documents({}),
        'categories': categories_collection.count_documents({})
    }
    recent_orders = list(orders_collection.find().sort('created_at', -1).limit(20))
    for order in recent_orders:
        user_id_raw = order.get('user_id')
        
        # 1. Chuyển đổi String -> ObjectId để tìm trong bảng Users
        try:
            # Nếu nó là chuỗi thì ép kiểu sang ObjectId, nếu không thì giữ nguyên
            if isinstance(user_id_raw, str):
                query_id = ObjectId(user_id_raw)
            else:
                query_id = user_id_raw
                
            user_info = users_collection.find_one({'_id': query_id})
        except:
            user_info = None

        # 2. Gán dữ liệu vào đơn hàng để hiển thị
        if user_info:
            order['customer_email'] = user_info.get('email')
            order['customer_name'] = user_info.get('full_name', user_info.get('name'))
        else:
            order['customer_email'] = 'Khách vãng lai / Đã xóa'
    users = list(users_collection.find().sort('created_at', -1))
    return render_template('admin/dashboard.html', stats=stats, orders=recent_orders, users=users, page='dashboard')

# --- ROUTE XÓA USER ---
@app.route('/admin/delete_user/<user_id>')
def delete_user(user_id):
    # 1. Kiểm tra quyền Admin
    if not is_admin():
        flash("Bạn không có quyền thực hiện thao tác này.", "error")
        return redirect(url_for('home'))

    # 2. [QUAN TRỌNG] Không cho Admin tự xóa chính mình để tránh lỗi hệ thống
    if user_id == session.get('user_id'):
        flash("Không thể tự xóa tài khoản đang đăng nhập!", "error")
        return redirect(url_for('admin_dashboard'))

    try:
        # 3. Thực hiện xóa
        users_collection.delete_one({'_id': ObjectId(user_id)})
        flash("Đã xóa tài khoản user thành công!", "success")
    except Exception as e:
        flash(f"Lỗi khi xóa: {str(e)}", "error")

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/products', methods=['GET', 'POST'])
def admin_products():
    if not is_admin(): return redirect('/')
    
    if request.method == 'POST':
        # Xử lý ảnh
        img1 = request.form.get('image1')
        img2 = request.form.get('image2')
        img3 = request.form.get('image3')
        if not img1: img1 = request.form.get('image')
        images_list = [img for img in [img1, img2, img3] if img and img.strip() != '']
        if not images_list: images_list = ["https://via.placeholder.com/300"]

        # [FIX] Lấy thông tin Category (Giả sử form gửi về category_name)
        cat_name = request.form.get('category')
        cat_obj = categories_collection.find_one({'name': cat_name})
        cat_id = cat_obj['_id'] if cat_obj else None

        # [FIX QUAN TRỌNG] Tạo cấu trúc Attributes để khớp với DB mới
        new_product = {
            "name": request.form.get('name'),
            "price": float(request.form.get('price')),
            "stock": int(request.form.get('stock', 10)),
            
            # Cấu trúc mới: Lưu cả ID và Name
            "category_id": cat_id, 
            "category_name": cat_name,
            
            # Cấu trúc mới: attributes lồng nhau
            "attributes": {
                "brand": request.form.get('brand'),
                "gender": request.form.get('gender'), # men, women, unisex
                "material": "Standard" # Mặc định hoặc thêm ô nhập liệu nếu cần
            },
            
            "descriptions": request.form.get('description'), # Lưu vào descriptions (số nhiều) cho chuẩn init_db_v2
            "images": images_list,
            "tags": [cat_name.lower(), request.form.get('brand').lower()],
            "created_at": datetime.now()
        }

        products_collection.insert_one(new_product)
        flash("New product created successfully (Standard Format)!", "success")
        return redirect('/admin/products')
    
    products = list(products_collection.find().sort('created_at', -1))
    categories = list(categories_collection.find()) 
    return render_template('admin/products.html', products=products, categories=categories, page='products')

@app.route('/admin/products/edit/<product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    if not is_admin(): return redirect('/')
    try: p_id = ObjectId(product_id)
    except: return "Invalid ID"

    if request.method == 'POST':
        img1, img2, img3 = request.form.get('image1'), request.form.get('image2'), request.form.get('image3')
        if not img1: img1 = request.form.get('image')
        images_list = [img for img in [img1, img2, img3] if img and img.strip() != '']
        
        # [FIX] Cập nhật theo cấu trúc lồng nhau
        updated_data = {
            "name": request.form.get('name'),
            "price": float(request.form.get('price')),
            "stock": int(request.form.get('stock')),
            "descriptions": request.form.get('description'),
            
            # Cập nhật attributes
            "attributes.brand": request.form.get('brand'),
            "attributes.gender": request.form.get('gender'),
            
            "category_name": request.form.get('category')
        }
        
        if images_list: updated_data["images"] = images_list
        
        # Sử dụng $set để update từng trường
        products_collection.update_one({'_id': p_id}, {'$set': updated_data})
        
        flash("Product updated successfully!", "success")
        return redirect('/admin/products')
    
    product = products_collection.find_one({'_id': p_id})
    categories = list(categories_collection.find()) 
    return render_template('admin/edit_product.html', product=product, categories=categories)

@app.route('/admin/products/delete/<product_id>')
def admin_delete_product(product_id):
    if not is_admin(): return redirect('/')
    products_collection.delete_one({'_id': ObjectId(product_id)})
    flash("Product deleted permanently.", "warning")
    return redirect('/admin/products')

@app.route('/admin/users')
def admin_users():
    if not is_admin(): return redirect('/')
    users = list(users_collection.find())
    return render_template('admin/users.html', users=users, page='users')

# --- HELPERS ---
@app.context_processor
def inject_global_data():
    data = {'cart_count': 0, 'current_user': None, 'is_admin_user': False}
    
    if 'user_id' in session:
        # Cart Count
        user_cart = db['carts'].find_one({'user_id': session['user_id']})
        if user_cart:
            data['cart_count'] = sum(item['quantity'] for item in user_cart.get('items', []))
        
        # User Info
        user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
        if user:
            data['current_user'] = user
            data['is_admin_user'] = (user.get('email') == ADMIN_EMAIL)
            
    return data

@app.route('/admin/categories', methods=['GET', 'POST'])
def admin_categories():
    if not is_admin(): return redirect('/')
    
    if request.method == 'POST':
        # Thêm danh mục mới
        name = request.form.get('name')
        if name:
            categories_collection.insert_one({
                "name": name,
                "created_at": datetime.now()
            })
            flash("New category added!", "success")
        return redirect('/admin/categories')

    # Lấy danh sách danh mục
    categories = list(categories_collection.find().sort('created_at', -1))
    return render_template('admin/categories.html', categories=categories, page='categories')

@app.route('/admin/categories/delete/<cat_id>')
def delete_category(cat_id):
    if not is_admin(): return redirect('/')
    categories_collection.delete_one({'_id': ObjectId(cat_id)})
    flash("Category deleted.", "success")
    return redirect('/admin/categories')

# --- ADMIN: CẬP NHẬT TRẠNG THÁI ĐƠN HÀNG ---
@app.route('/admin/order/update-status/<order_id>/<new_status>')
def update_order_status(order_id, new_status):
    # 1. Kiểm tra quyền Admin
    if not is_admin():
        return "Access Denied"
    
    # 2. Các trạng thái hợp lệ
    valid_statuses = ['pending', 'shipping', 'delivered', 'cancelled']
    
    if new_status in valid_statuses:
        # 3. Cập nhật trong MongoDB
        orders_collection.update_one(
            {'_id': ObjectId(order_id)},
            {'$set': {'status': new_status}}
        )
        flash(f"Order status updated to {new_status}", "success")
    else:
        flash("Invalid status", "error")
        
    # Quay lại trang danh sách đơn hàng
    return redirect(url_for('admin_dashboard'))

# --- 8. CHECKOUT & PAYMENT ROUTES ---

# BƯỚC 1: ĐIỀN THÔNG TIN GIAO HÀNG
@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    # [QUAN TRỌNG] Lấy thông tin đầy đủ của User từ DB để điền vào Form
    current_user = users_collection.find_one({'_id': ObjectId(session['user_id'])})
    
    user_cart = db['carts'].find_one({'user_id': session['user_id']})
    
    # Kiểm tra giỏ hàng rỗng (chỉ check khi không phải là POST thanh toán)
    if request.method == 'GET' and (not user_cart or not user_cart.get('items')):
        # Nếu session đang lưu hàng checkout thì không sao, còn không thì báo rỗng
        if not session.get('checkout_items'):
            flash("Your cart is empty!", "warning")
            return redirect(url_for('view_cart'))
    
    # --- TRƯỜNG HỢP 1: POST TỪ GIỎ HÀNG (Người dùng vừa bấm Checkout) ---
    if request.method == 'POST' and request.form.get('from_cart') == 'true':
        selected_ids = request.form.getlist('selected_items') 
        
        if not selected_ids:
            flash("Please select at least one item to checkout.", "warning")
            return redirect(url_for('view_cart'))
            
        checkout_items = []
        if user_cart:
            for item in user_cart['items']:
                if str(item['product_id']) in selected_ids:
                    # Tạo bản sao và chuyển ObjectId thành String để lưu Session
                    item_copy = item.copy()
                    item_copy['product_id'] = str(item['product_id']) 
                    checkout_items.append(item_copy)
        
        # Lưu vào Session
        session['checkout_items'] = checkout_items
        
        total_price = sum(item['price'] * item['quantity'] for item in checkout_items)
        
        # [FIX] Truyền biến 'user' chứa object đầy đủ vào template
        return render_template('checkout.html', items=checkout_items, total=total_price, user=current_user)

    # --- TRƯỜNG HỢP 2: POST TỪ TRANG CHECKOUT (Người dùng điền form ship) ---
    elif request.method == 'POST':
        items = session.get('checkout_items', [])
        if not items: return redirect(url_for('view_cart'))

        session['shipping_info'] = {
            'fullname': request.form.get('fullname'),
            'phone': request.form.get('phone'),
            'address': request.form.get('address'),
            'note': request.form.get('note')
        }
        
        payment_method = request.form.get('payment_method')
        session['payment_method'] = payment_method

        if payment_method == 'MoMo QR':
            return redirect(url_for('payment_momo'))
        else:
            return redirect(url_for('place_order'))

    # --- TRƯỜNG HỢP 3: GET (Load lại trang) ---
    else:
        items = session.get('checkout_items', [])
        if not items: return redirect(url_for('view_cart'))
        
        total_price = sum(item['price'] * item['quantity'] for item in items)
        
        # [FIX] Truyền biến 'user' vào template
        return render_template('checkout.html', items=items, total=total_price, user=current_user)


# BƯỚC 2: MÀN HÌNH QUÉT MÃ MOMO
@app.route('/payment/momo')
def payment_momo():
    if 'user_id' not in session or 'checkout_items' not in session:
        return redirect(url_for('checkout'))
        
    items = session['checkout_items']
    total_price = sum(item['price'] * item['quantity'] for item in items)
    
    return render_template('payment_momo.html', total=total_price)


# BƯỚC 3: CHỐT ĐƠN & XÓA MÓN ĐÃ MUA
@app.route('/place-order', methods=['GET', 'POST'])
def place_order():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    items_to_buy = session.get('checkout_items', [])
    shipping_info = session.get('shipping_info')
    payment_method = session.get('payment_method', 'COD')
    
    if not items_to_buy or not shipping_info:
        return redirect(url_for('home'))

    total_price = sum(item['price'] * item['quantity'] for item in items_to_buy)

    # 1. TẠO ĐƠN HÀNG
    # (Lưu ý: items_to_buy đang chứa product_id dạng String, MongoDB vẫn chấp nhận lưu string)
    new_order = {
        'user_id': session['user_id'],
        'items': items_to_buy,
        'total_price': total_price,
        'shipping_info': shipping_info,
        'payment_method': payment_method,
        'status': 'Pending' if payment_method == 'COD' else 'Paid',
        'created_at': datetime.now()
    }
    orders_collection.insert_one(new_order)

    # 2. XÓA MÓN ĐÃ MUA KHỎI GIỎ HÀNG
    # [QUAN TRỌNG] Phải chuyển String về lại ObjectId để tìm và xóa trong DB
    bought_ids = [ObjectId(item['product_id']) for item in items_to_buy]
    
    db['carts'].update_one(
        {'user_id': session['user_id']},
        {'$pull': {'items': {'product_id': {'$in': bought_ids}}}}
    )

    # 3. DỌN SESSION
    session.pop('checkout_items', None)
    session.pop('shipping_info', None)

    return redirect(url_for('order_success'))

# --- [MỚI] ROUTE ORDER SUCCESS ---
@app.route('/order-success')
def order_success():
    return render_template('order_success.html')

# --- 9. ROUTE TÌM KIẾM (ĐÃ CẬP NHẬT) ---
@app.route('/search')
def search():
    query = request.args.get('q', '')
    
    if query:
        # [CẬP NHẬT] Tìm kiếm đa trường theo cấu trúc mới
        products = list(products_collection.find({
            "$or": [
                {"name": {"$regex": query, "$options": "i"}},
                {"category_name": {"$regex": query, "$options": "i"}}, # DB mới có field này
                {"attributes.brand": {"$regex": query, "$options": "i"}}, # Brand nằm trong attributes
                {"tags": {"$regex": query, "$options": "i"}}
            ]
        }))
    else:
        products = []

    return render_template('index.html', products=products, search_query=query, page_title="Search Results")


# --- 10. ROUTE INIT DỮ LIỆU GIẢ LẬP (INTERACTIONS) ---
@app.route('/init-interactions')
def init_interactions():
    all_products = list(products_collection.find())
    product_ids = [p['_id'] for p in all_products]
    if not product_ids: return "Run /init-db first!"
    
    # Tạo user giả
    dummy_users = []
    for i in range(1, 21):
        email = f"user{i}@example.com"
        if not users_collection.find_one({'email': email}):
            user_data = {'email': email, 'password': '123', 'name': f"User {i}"}
            users_collection.insert_one(user_data)
            dummy_users.append(user_data)
    
    # Tạo view giả
    interactions = []
    all_users = list(users_collection.find({'email': {'$regex': '@example.com'}}))
    for _ in range(500):
        interactions.append({
            "user_id": str(random.choice(all_users)['_id']),
            "product_id": random.choice(product_ids),
            "action": "view",
            "timestamp": datetime.now()
        })
    interactions_collection.insert_many(interactions)
    return "Dummy Data Created!"

# --- 11. ROUTE: BẮT ĐẦU ĐĂNG NHẬP GOOGLE ---
@app.route('/login/google')
def login_google():
    # Chuyển hướng người dùng sang trang Google
    redirect_uri = url_for('authorize_google', _external=True)
    return google.authorize_redirect(redirect_uri)

# --- ROUTE: GOOGLE GỌI LẠI (CALLBACK) ---
@app.route('/authorize')
def authorize_google():
    try:
        # 1. Lấy Token từ Google
        token = google.authorize_access_token()
        
        # 2. Lấy thông tin User
        resp = google.get('https://www.googleapis.com/oauth2/v3/userinfo')
        user_info = resp.json()
        
        user_email = user_info['email']
        user_name = user_info['name']
        
        # 3. Kiểm tra User trong MongoDB
        existing_user = users_collection.find_one({'email': user_email})
        
        if not existing_user:
            # Nếu chưa có -> Tự động Đăng ký
            new_user = {
                'email': user_email,
                'password': None, # Không có pass vì dùng Google
                'full_name': user_name,
                'role': 'customer',
                'auth_provider': 'google', # Đánh dấu nick Google
                'created_at': datetime.now()
            }
            users_collection.insert_one(new_user)
            session['user_id'] = str(new_user['_id'])
            flash(f"Account created via Google! Welcome {user_name}.", "success")
            return redirect(url_for('onboarding'))
        else:
            # Nếu có rồi -> Đăng nhập luôn
            session['user_id'] = str(existing_user['_id'])
            
            if not existing_user.get('is_onboarded'):
                return redirect(url_for('onboarding'))

            flash(f"Welcome back, {user_name}!", "success")
        
        return redirect(url_for('home'))
        
    except Exception as e:
        # Xử lý lỗi nếu Google từ chối hoặc sai cấu hình
        flash(f"Google Login failed: {str(e)}", "error")
        return redirect(url_for('login'))

# --- 12. ROUTE USER PROFILE ---
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    user_id = session['user_id']
    
    # XỬ LÝ CẬP NHẬT THÔNG TIN
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        phone = request.form.get('phone')
        address = request.form.get('address')

        new_avatar = request.form.get('avatar_url')

        update_data = {
            'full_name': full_name,
            'phone': phone,
            'address': address
        }

        if new_avatar:
            update_data['avatar'] = new_avatar
        
        users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': update_data}
        )
        flash("Profile updated successfully!", "success")
        return redirect(url_for('profile'))

    # LẤY DỮ LIỆU HIỂN THỊ
    user = users_collection.find_one({'_id': ObjectId(user_id)})
    
    # 1. Tạo Avatar tự động (Nếu chưa có)
    # Dùng email làm "hạt giống" (seed) để avatar cố định, không bị đổi mỗi lần F5
    if not user.get('avatar'):
        seed = user.get('email', 'guest')
        # Style 'notionists' rất đẹp và hiện đại
        user['avatar'] = f"https://api.dicebear.com/7.x/notionists/svg?seed={seed}" 

    # 2. Lấy lịch sử đơn hàng của user này
    my_orders = list(orders_collection.find({'user_id': user_id}).sort('created_at', -1))

    return render_template('profile.html', user=user, orders=my_orders)

# --- 11. ROUTE ONBOARDING (THU THẬP SỞ THÍCH) ---
@app.route('/onboarding', methods=['GET', 'POST'])
def onboarding():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        # Lấy dữ liệu từ form
        selected_gender = request.form.get('gender') # Men / Women
        selected_styles = request.form.getlist('styles') # ['Streetwear', 'Vintage', ...]
        selected_categories = request.form.getlist('categories') # ['Hoodies', 'Shoes', ...]
        
        # Lưu vào preferences của user
        users_collection.update_one(
            {'_id': ObjectId(session['user_id'])},
            {'$set': {
                'preferences': {
                    'gender': selected_gender,
                    'styles': selected_styles,
                    'categories': selected_categories
                },
                'is_onboarded': True
            }}
        )
        
        # Sau khi lưu xong, về trang chủ
        flash("AI has curated a personalized feed just for you!", "success")
        return redirect(url_for('home'))

    return render_template('onboarding.html')

if __name__ == '__main__':
    app.run(debug=True)