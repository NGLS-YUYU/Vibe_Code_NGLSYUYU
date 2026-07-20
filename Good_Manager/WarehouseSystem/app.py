"""
仓库管理系统 — Warehouse Management System
基于 Flask + SQLAlchemy + SQLite + Flask-Login 的局域网多人协作版
"""

import os
import sys
import socket
from datetime import datetime, date, timedelta
from io import BytesIO
from functools import wraps

import pandas as pd
from flask import (
    Flask, flash, jsonify, redirect, render_template, request,
    send_file, session, url_for,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from waitress import serve

import config

# ── 应用初始化 ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = config.DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)  # 8 小时后自动退出

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "请先登录后再访问"
login_manager.login_message_category = "warning"

# 路径处理（兼容 EXE 打包）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ── 数据库模型 ────────────────────────────────────────────────────────────────

class User(db.Model, UserMixin):
    """用户模型"""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    real_name = db.Column(db.String(64), default="")
    role = db.Column(db.String(16), nullable=False, default="guest")  # admin / operator / guest
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_login = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


class Product(db.Model):
    """商品模型"""
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(128), nullable=False)
    sku = db.Column(db.String(64), unique=True, nullable=False, index=True)
    category = db.Column(db.String(64), default="")
    unit = db.Column(db.String(16), default="件")
    quantity = db.Column(db.Integer, nullable=False, default=0)
    location = db.Column(db.String(64), default="")
    min_stock = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now)
    is_deleted = db.Column(db.Integer, nullable=False, default=0, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<Product {self.name}>"


class InboundRecord(db.Model):
    """入库记录模型"""
    __tablename__ = "inbound_records"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.Integer, nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    supplier = db.Column(db.String(128), default="")
    operator = db.Column(db.String(64), default="")
    remark = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    is_deleted = db.Column(db.Integer, nullable=False, default=0, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    product = db.relationship(
        "Product",
        primaryjoin="InboundRecord.product_id == Product.id",
        foreign_keys=[product_id],
        lazy="joined",
    )

    def __repr__(self):
        return f"<InboundRecord {self.id}>"


class OutboundRecord(db.Model):
    """出库记录模型"""
    __tablename__ = "outbound_records"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.Integer, nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    recipient = db.Column(db.String(128), default="")
    operator = db.Column(db.String(64), default="")
    remark = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    is_deleted = db.Column(db.Integer, nullable=False, default=0, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    product = db.relationship(
        "Product",
        primaryjoin="OutboundRecord.product_id == Product.id",
        foreign_keys=[product_id],
        lazy="joined",
    )

    def __repr__(self):
        return f"<OutboundRecord {self.id}>"


# ── Flask-Login 用户加载 ─────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── 权限装饰器 ────────────────────────────────────────────────────────────────

def role_required(*roles):
    """限制指定角色才能访问的装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("login"))
            if current_user.role not in roles:
                flash("您没有权限访问此页面", "danger")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ── 模板上下文注入（所有模板可用 current_user） ──────────────────────────────

@app.context_processor
def inject_user():
    return {"current_user": current_user}


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def cleanup_expired():
    """物理删除超过配置天数的软删除记录（三张表）"""
    cutoff = datetime.now() - timedelta(days=config.AUTO_CLEANUP_DAYS)
    for model in (Product, InboundRecord, OutboundRecord):
        model.query.filter(
            model.is_deleted == 1,
            model.deleted_at.isnot(None),
            model.deleted_at < cutoff,
        ).delete(synchronize_session=False)
    db.session.commit()


def init_db():
    """初始化数据库：建表 + 创建默认账号 + 清理过期记录"""
    db.create_all()

    default_users = [
        {"username": "admin_H", "password": "admin001", "real_name": "高级管理员", "role": "admin"},
        {"username": "admin_M", "password": "admin002", "real_name": "操作员",     "role": "operator"},
        {"username": "admin_L", "password": "admin003", "real_name": "访客",       "role": "guest"},
    ]

    created = 0
    for u in default_users:
        if not User.query.filter_by(username=u["username"]).first():
            db.session.add(User(
                username=u["username"],
                password_hash=generate_password_hash(u["password"]),
                real_name=u["real_name"],
                role=u["role"],
                created_at=datetime.now(),
            ))
            created += 1

    if created > 0:
        db.session.commit()
        print(f"[OK] {created} 个默认账号已创建：")
        for u in default_users:
            print(f"       ({u['role']})")

    # 启动时清理过期记录
    cleanup_expired()


# ── 工具：格式化 LEFT JOIN 结果 ──────────────────────────────────────────────

def _prod_name(prod):
    """返回商品名称或占位文本"""
    return prod.name if prod else "(已删除商品)"


def _prod_sku(prod):
    return prod.sku if prod else "-"


def _prod_unit(prod):
    return prod.unit if prod else "件"


def _fmt_time(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


# ── 登录 / 登出 ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页面"""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("请输入用户名和密码", "danger")
            return render_template("login.html")

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            user.last_login = datetime.now()
            db.session.commit()
            session.permanent = True  # 8 小时后过期（见配置）
            flash(f"欢迎回来，{user.real_name or user.username}！", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        else:
            flash("用户名或密码错误", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    """退出登录"""
    logout_user()
    flash("您已退出登录", "info")
    return redirect(url_for("login"))


# ── 首页 / 仪表盘 ────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    """首页仪表盘：库存总览与关键指标"""
    today_start = date.today()
    today_end = today_start + timedelta(days=1)

    # 商品种类数
    total_products = Product.query.filter_by(is_deleted=0).count()

    # 库存总量
    total_stock = db.session.query(
        db.func.coalesce(db.func.sum(Product.quantity), 0)
    ).filter(Product.is_deleted == 0).scalar()

    # 今日入库
    today_inbound = db.session.query(
        db.func.coalesce(db.func.sum(InboundRecord.quantity), 0)
    ).filter(
        InboundRecord.is_deleted == 0,
        InboundRecord.created_at >= today_start,
        InboundRecord.created_at < today_end,
    ).scalar()

    # 今日出库
    today_outbound = db.session.query(
        db.func.coalesce(db.func.sum(OutboundRecord.quantity), 0)
    ).filter(
        OutboundRecord.is_deleted == 0,
        OutboundRecord.created_at >= today_start,
        OutboundRecord.created_at < today_end,
    ).scalar()

    # 低库存预警
    low_stock = Product.query.filter(
        Product.is_deleted == 0,
        Product.quantity <= Product.min_stock,
        Product.min_stock > 0,
    ).count()

    # 低库存明细（Top 10）
    low_stock_items = Product.query.filter(
        Product.is_deleted == 0,
        Product.quantity <= Product.min_stock,
        Product.min_stock > 0,
    ).order_by(
        db.cast(Product.quantity, db.Float) / Product.min_stock
    ).limit(10).all()

    # 最近入库（LEFT JOIN 确保已删除商品的历史记录也能显示）
    recent_inbound = db.session.query(InboundRecord, Product).outerjoin(
        Product, InboundRecord.product_id == Product.id
    ).filter(
        InboundRecord.is_deleted == 0,
    ).order_by(InboundRecord.id.desc()).limit(5).all()

    # 最近出库（LEFT JOIN）
    recent_outbound = db.session.query(OutboundRecord, Product).outerjoin(
        Product, OutboundRecord.product_id == Product.id
    ).filter(
        OutboundRecord.is_deleted == 0,
    ).order_by(OutboundRecord.id.desc()).limit(5).all()

    # 格式化最近记录
    def fmt_recent(rows):
        result = []
        for record, prod in rows:
            result.append({
                "name":        _prod_name(prod),
                "sku":         _prod_sku(prod),
                "quantity":    record.quantity,
                "supplier":    getattr(record, "supplier", ""),
                "recipient":   getattr(record, "recipient", ""),
                "operator":    record.operator,
                "created_at":  _fmt_time(record.created_at),
            })
        return result

    return render_template(
        "index.html",
        total_products=total_products,
        total_stock=total_stock or 0,
        today_inbound=today_inbound or 0,
        today_outbound=today_outbound or 0,
        low_stock=low_stock,
        low_stock_items=low_stock_items,
        recent_inbound=fmt_recent(recent_inbound),
        recent_outbound=fmt_recent(recent_outbound),
    )


# ── 商品管理 API ────────────────────────────────────────────────────────────

@app.route("/api/products")
@login_required
def api_products():
    """获取所有未删除商品（JSON），供前端下拉框使用"""
    prods = Product.query.filter_by(is_deleted=0).order_by(Product.name).all()
    return jsonify([{
        "id": p.id, "name": p.name, "sku": p.sku,
        "quantity": p.quantity, "unit": p.unit,
    } for p in prods])


@app.route("/api/product", methods=["POST"])
@login_required
@role_required("admin")
def api_add_product():
    """新增商品（仅 admin）"""
    data = request.get_json()
    name = data.get("name", "").strip()
    sku = data.get("sku", "").strip()
    if not name or not sku:
        return jsonify({"ok": False, "msg": "商品名称和编码不能为空"})

    # 编码唯一性检查
    if Product.query.filter_by(sku=sku, is_deleted=0).first():
        return jsonify({"ok": False, "msg": f"商品编码「{sku}」已存在"})

    now = datetime.now()
    product = Product(
        name=name,
        sku=sku,
        category=data.get("category", "").strip(),
        unit=data.get("unit", "件").strip() or "件",
        quantity=int(data.get("quantity", 0)),
        location=data.get("location", "").strip(),
        min_stock=int(data.get("min_stock", 0)),
        created_at=now,
        updated_at=now,
    )
    db.session.add(product)
    db.session.commit()
    return jsonify({"ok": True, "msg": f"商品「{name}」添加成功"})


# ── 商品管理页面 ────────────────────────────────────────────────────────────

@app.route("/products")
@login_required
@role_required("admin", "operator")
def products_page():
    """商品管理页面：展示所有未删除商品"""
    products = Product.query.filter_by(is_deleted=0).order_by(Product.name).all()
    return render_template("products.html", products=products)


# ── 商品 软删除 / 恢复 / 永久删除 API ───────────────────────────────────────

@app.route("/api/product/<int:pid>/delete", methods=["POST"])
@login_required
@role_required("admin")
def api_product_soft_delete(pid):
    """软删除商品（不影响已关联的出入库历史记录）"""
    product = Product.query.filter_by(id=pid, is_deleted=0).first()
    if not product:
        return jsonify({"ok": False, "msg": "商品不存在或已被删除"})
    product.is_deleted = 1
    product.deleted_at = datetime.now()
    db.session.commit()
    return jsonify({"ok": True, "msg": "商品已移入回收站"})


@app.route("/api/product/<int:pid>/restore", methods=["POST"])
@login_required
@role_required("admin")
def api_product_restore(pid):
    """从回收站恢复商品"""
    product = Product.query.filter_by(id=pid, is_deleted=1).first()
    if not product:
        return jsonify({"ok": False, "msg": "商品不存在或未被删除"})
    product.is_deleted = 0
    product.deleted_at = None
    db.session.commit()
    return jsonify({"ok": True, "msg": "商品已恢复"})


@app.route("/api/product/<int:pid>/permanent_delete", methods=["POST"])
@login_required
@role_required("admin")
def api_product_permanent_delete(pid):
    """永久删除商品：直接物理删除（出入库记录保留原 product_id，LEFT JOIN + COALESCE 显示）"""
    product = Product.query.filter_by(id=pid, is_deleted=1).first()
    if not product:
        return jsonify({
            "ok": False,
            "msg": "商品不存在或未被软删除（只能永久删除回收站中的商品）",
        })
    name = product.name
    db.session.delete(product)
    db.session.commit()
    return jsonify({
        "ok": True,
        "msg": f"商品「{name}」已永久删除，关联的出入库记录已保留",
    })


# ── 入库操作 ────────────────────────────────────────────────────────────────

@app.route("/inbound", methods=["GET", "POST"])
@login_required
@role_required("admin", "operator")
def inbound():
    """入库操作页面"""
    if request.method == "POST":
        product_id = request.form.get("product_id", type=int)
        quantity = request.form.get("quantity", type=int)
        supplier = request.form.get("supplier", "").strip()
        operator = request.form.get("operator", "").strip()
        remark = request.form.get("remark", "").strip()

        if not product_id:
            flash("请选择商品", "danger")
            return redirect(url_for("inbound"))
        if not quantity or quantity <= 0:
            flash("入库数量必须大于 0", "danger")
            return redirect(url_for("inbound"))

        product = Product.query.filter_by(id=product_id, is_deleted=0).first()
        if not product:
            flash("商品不存在或已被删除", "danger")
            return redirect(url_for("inbound"))

        now = datetime.now()
        record = InboundRecord(
            product_id=product_id,
            quantity=quantity,
            supplier=supplier,
            operator=operator,
            remark=remark,
            created_at=now,
        )
        product.quantity += quantity
        product.updated_at = now
        db.session.add(record)
        db.session.commit()

        flash(f"入库成功：{product.name} +{quantity} {product.unit}", "success")
        return redirect(url_for("inbound"))

    # GET：加载商品列表
    products = Product.query.filter_by(is_deleted=0).order_by(Product.name).all()
    return render_template("inbound.html", products=products)


# ── 出库操作 ────────────────────────────────────────────────────────────────

@app.route("/outbound", methods=["GET", "POST"])
@login_required
@role_required("admin", "operator")
def outbound():
    """出库操作页面"""
    if request.method == "POST":
        product_id = request.form.get("product_id", type=int)
        quantity = request.form.get("quantity", type=int)
        recipient = request.form.get("recipient", "").strip()
        operator = request.form.get("operator", "").strip()
        remark = request.form.get("remark", "").strip()

        if not product_id:
            flash("请选择商品", "danger")
            return redirect(url_for("outbound"))
        if not quantity or quantity <= 0:
            flash("出库数量必须大于 0", "danger")
            return redirect(url_for("outbound"))

        product = Product.query.filter_by(id=product_id, is_deleted=0).first()
        if not product:
            flash("商品不存在或已被删除", "danger")
            return redirect(url_for("outbound"))
        if product.quantity < quantity:
            flash(
                f"库存不足！「{product.name}」当前库存：{product.quantity}，需要：{quantity}",
                "danger",
            )
            return redirect(url_for("outbound"))

        now = datetime.now()
        record = OutboundRecord(
            product_id=product_id,
            quantity=quantity,
            recipient=recipient,
            operator=operator,
            remark=remark,
            created_at=now,
        )
        product.quantity -= quantity
        product.updated_at = now
        db.session.add(record)
        db.session.commit()

        flash(f"出库成功：{product.name} -{quantity} {product.unit}", "success")
        return redirect(url_for("outbound"))

    # GET：加载商品列表
    products = Product.query.filter_by(is_deleted=0).order_by(Product.name).all()
    return render_template("outbound.html", products=products)


# ── 实时库存查询 ────────────────────────────────────────────────────────────

@app.route("/stock")
@login_required
def stock():
    """实时库存查询页面（支持搜索/筛选）"""
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    low_only = request.args.get("low_only", "").strip()

    # 获取所有分类
    categories = db.session.query(
        Product.category
    ).filter(
        Product.category != "",
        Product.is_deleted == 0,
    ).distinct().order_by(Product.category).all()

    # 构建查询
    q = Product.query.filter(Product.is_deleted == 0)

    if search:
        like = f"%{search}%"
        q = q.filter(db.or_(
            Product.name.ilike(like),
            Product.sku.ilike(like),
            Product.location.ilike(like),
        ))
    if category:
        q = q.filter(Product.category == category)
    if low_only == "1":
        q = q.filter(
            Product.quantity <= Product.min_stock,
            Product.min_stock > 0,
        )

    products = q.order_by(Product.name).all()

    return render_template(
        "stock.html",
        products=products,
        categories=categories,
        search=search,
        selected_category=category,
        low_only=low_only,
    )


# ── 每日入库明细 ────────────────────────────────────────────────────────────

@app.route("/daily_inbound")
@login_required
def daily_inbound():
    """每日入库明细页（LEFT JOIN 确保已删除商品的历史记录也能显示）"""
    query_date_str = request.args.get("date", date.today().isoformat())
    try:
        query_date = datetime.strptime(query_date_str, "%Y-%m-%d").date()
    except ValueError:
        query_date = date.today()
        query_date_str = query_date.isoformat()

    next_day = query_date + timedelta(days=1)

    rows = db.session.query(InboundRecord, Product).outerjoin(
        Product, InboundRecord.product_id == Product.id
    ).filter(
        InboundRecord.is_deleted == 0,
        InboundRecord.created_at >= query_date,
        InboundRecord.created_at < next_day,
    ).order_by(InboundRecord.id.desc()).all()

    total_qty = db.session.query(
        db.func.coalesce(db.func.sum(InboundRecord.quantity), 0)
    ).filter(
        InboundRecord.is_deleted == 0,
        InboundRecord.created_at >= query_date,
        InboundRecord.created_at < next_day,
    ).scalar()

    total_count = InboundRecord.query.filter(
        InboundRecord.is_deleted == 0,
        InboundRecord.created_at >= query_date,
        InboundRecord.created_at < next_day,
    ).count()

    records = []
    for record, prod in rows:
        records.append({
            "id": record.id,
            "name": _prod_name(prod),
            "sku": _prod_sku(prod),
            "unit": _prod_unit(prod),
            "quantity": record.quantity,
            "supplier": record.supplier,
            "operator": record.operator,
            "remark": record.remark,
            "created_at": _fmt_time(record.created_at),
        })

    return render_template(
        "daily_inbound.html",
        records=records,
        query_date=query_date_str,
        today_date=date.today().isoformat(),
        total_quantity=total_qty or 0,
        total_records=total_count,
    )


# ── 每日出库明细 ────────────────────────────────────────────────────────────

@app.route("/daily_outbound")
@login_required
def daily_outbound():
    """每日出库明细页（LEFT JOIN 确保已删除商品的历史记录也能显示）"""
    query_date_str = request.args.get("date", date.today().isoformat())
    try:
        query_date = datetime.strptime(query_date_str, "%Y-%m-%d").date()
    except ValueError:
        query_date = date.today()
        query_date_str = query_date.isoformat()

    next_day = query_date + timedelta(days=1)

    rows = db.session.query(OutboundRecord, Product).outerjoin(
        Product, OutboundRecord.product_id == Product.id
    ).filter(
        OutboundRecord.is_deleted == 0,
        OutboundRecord.created_at >= query_date,
        OutboundRecord.created_at < next_day,
    ).order_by(OutboundRecord.id.desc()).all()

    total_qty = db.session.query(
        db.func.coalesce(db.func.sum(OutboundRecord.quantity), 0)
    ).filter(
        OutboundRecord.is_deleted == 0,
        OutboundRecord.created_at >= query_date,
        OutboundRecord.created_at < next_day,
    ).scalar()

    total_count = OutboundRecord.query.filter(
        OutboundRecord.is_deleted == 0,
        OutboundRecord.created_at >= query_date,
        OutboundRecord.created_at < next_day,
    ).count()

    records = []
    for record, prod in rows:
        records.append({
            "id": record.id,
            "name": _prod_name(prod),
            "sku": _prod_sku(prod),
            "unit": _prod_unit(prod),
            "quantity": record.quantity,
            "recipient": record.recipient,
            "operator": record.operator,
            "remark": record.remark,
            "created_at": _fmt_time(record.created_at),
        })

    return render_template(
        "daily_outbound.html",
        records=records,
        query_date=query_date_str,
        today_date=date.today().isoformat(),
        total_quantity=total_qty or 0,
        total_records=total_count,
    )


# ── 出入库记录 软删除 / 恢复 / 永久删除 API ──────────────────────────────

@app.route("/api/record/<rectype>/<int:rec_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def api_soft_delete(rectype, rec_id):
    """软删除一条出入库记录"""
    if rectype not in ("inbound", "outbound"):
        return jsonify({"ok": False, "msg": "无效的记录类型"})
    model = InboundRecord if rectype == "inbound" else OutboundRecord
    record = model.query.filter_by(id=rec_id, is_deleted=0).first()
    if not record:
        return jsonify({"ok": False, "msg": "记录不存在或已被删除"})
    record.is_deleted = 1
    record.deleted_at = datetime.now()
    db.session.commit()
    return jsonify({"ok": True, "msg": "已移入回收站"})


@app.route("/api/record/<rectype>/<int:rec_id>/restore", methods=["POST"])
@login_required
@role_required("admin")
def api_restore(rectype, rec_id):
    """从回收站恢复一条记录"""
    if rectype not in ("inbound", "outbound"):
        return jsonify({"ok": False, "msg": "无效的记录类型"})
    model = InboundRecord if rectype == "inbound" else OutboundRecord
    record = model.query.filter_by(id=rec_id, is_deleted=1).first()
    if not record:
        return jsonify({"ok": False, "msg": "记录不存在或未被删除"})
    record.is_deleted = 0
    record.deleted_at = None
    db.session.commit()
    return jsonify({"ok": True, "msg": "已恢复记录"})


@app.route("/api/record/<rectype>/<int:rec_id>/permanent_delete", methods=["POST"])
@login_required
@role_required("admin")
def api_permanent_delete(rectype, rec_id):
    """永久删除一条出入库记录（物理删除，不可恢复）"""
    if rectype not in ("inbound", "outbound"):
        return jsonify({"ok": False, "msg": "无效的记录类型"})
    model = InboundRecord if rectype == "inbound" else OutboundRecord
    record = model.query.filter_by(id=rec_id, is_deleted=1).first()
    if not record:
        return jsonify({"ok": False, "msg": "记录不存在或未被软删除"})
    db.session.delete(record)
    db.session.commit()
    return jsonify({"ok": True, "msg": "已永久删除，不可恢复"})


# ── 回收站页面 ────────────────────────────────────────────────────────────────

@app.route("/recycle_bin")
@login_required
@role_required("admin")
def recycle_bin():
    """回收站：统一展示已软删除的商品、入库、出库记录"""
    # 先清理过期记录
    cleanup_expired()

    # 已删除的商品
    deleted_products = Product.query.filter_by(is_deleted=1).order_by(
        Product.deleted_at.desc()
    ).all()

    # 已删除的入库记录
    deleted_inbound_raw = InboundRecord.query.filter_by(is_deleted=1).order_by(
        InboundRecord.deleted_at.desc()
    ).all()
    deleted_inbound = []
    for r in deleted_inbound_raw:
        prod = Product.query.get(r.product_id)
        deleted_inbound.append({
            "id": r.id,
            "name": _prod_name(prod),
            "sku": _prod_sku(prod),
            "unit": _prod_unit(prod),
            "quantity": r.quantity,
            "supplier": r.supplier,
            "operator": r.operator,
            "remark": r.remark,
            "created_at": _fmt_time(r.created_at),
            "deleted_at": _fmt_time(r.deleted_at),
        })

    # 已删除的出库记录
    deleted_outbound_raw = OutboundRecord.query.filter_by(is_deleted=1).order_by(
        OutboundRecord.deleted_at.desc()
    ).all()
    deleted_outbound = []
    for r in deleted_outbound_raw:
        prod = Product.query.get(r.product_id)
        deleted_outbound.append({
            "id": r.id,
            "name": _prod_name(prod),
            "sku": _prod_sku(prod),
            "unit": _prod_unit(prod),
            "quantity": r.quantity,
            "recipient": r.recipient,
            "operator": r.operator,
            "remark": r.remark,
            "created_at": _fmt_time(r.created_at),
            "deleted_at": _fmt_time(r.deleted_at),
        })

    return render_template(
        "recycle_bin.html",
        deleted_products=deleted_products,
        deleted_inbound=deleted_inbound,
        deleted_outbound=deleted_outbound,
    )


# ── 导出全部数据为 Excel ──────────────────────────────────────────────────

@app.route("/export_all")
@login_required
@role_required("admin", "operator")
def export_all():
    """生成包含三个工作表的 .xlsx 文件并下载（排除已删除记录）"""
    # Sheet 1: 实时库存
    stock_rows = Product.query.filter_by(is_deleted=0).order_by(Product.name).all()
    stock_data = []
    for p in stock_rows:
        stock_data.append({
            "商品编码": p.sku,
            "商品名称": p.name,
            "分类": p.category,
            "单位": p.unit,
            "当前库存": p.quantity,
            "库位": p.location,
            "安全下限": p.min_stock,
            "更新时间": _fmt_time(p.updated_at),
        })

    # Sheet 2: 全部入库明细
    inbound_rows = db.session.query(InboundRecord, Product).outerjoin(
        Product, InboundRecord.product_id == Product.id
    ).filter(InboundRecord.is_deleted == 0).order_by(
        InboundRecord.created_at.desc()
    ).all()
    inbound_data = []
    for r, p in inbound_rows:
        inbound_data.append({
            "入库时间": _fmt_time(r.created_at),
            "商品名称": _prod_name(p),
            "商品编码": _prod_sku(p),
            "单位": _prod_unit(p),
            "入库数量": r.quantity,
            "供应商": r.supplier,
            "操作人": r.operator,
            "备注": r.remark,
        })

    # Sheet 3: 全部出库明细
    outbound_rows = db.session.query(OutboundRecord, Product).outerjoin(
        Product, OutboundRecord.product_id == Product.id
    ).filter(OutboundRecord.is_deleted == 0).order_by(
        OutboundRecord.created_at.desc()
    ).all()
    outbound_data = []
    for r, p in outbound_rows:
        outbound_data.append({
            "出库时间": _fmt_time(r.created_at),
            "商品名称": _prod_name(p),
            "商品编码": _prod_sku(p),
            "单位": _prod_unit(p),
            "出库数量": r.quantity,
            "领用人/客户": r.recipient,
            "操作人": r.operator,
            "备注": r.remark,
        })

    stock_cols = ["商品编码", "商品名称", "分类", "单位", "当前库存", "库位", "安全下限", "更新时间"]
    inbound_cols = ["入库时间", "商品名称", "商品编码", "单位", "入库数量", "供应商", "操作人", "备注"]
    outbound_cols = ["出库时间", "商品名称", "商品编码", "单位", "出库数量", "领用人/客户", "操作人", "备注"]

    df_stock = pd.DataFrame(stock_data, columns=stock_cols) if stock_data else pd.DataFrame(columns=stock_cols)
    df_inbound = pd.DataFrame(inbound_data, columns=inbound_cols) if inbound_data else pd.DataFrame(columns=inbound_cols)
    df_outbound = pd.DataFrame(outbound_data, columns=outbound_cols) if outbound_data else pd.DataFrame(columns=outbound_cols)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_stock.to_excel(writer, sheet_name="实时库存", index=False)
        df_inbound.to_excel(writer, sheet_name="全部入库明细", index=False)
        df_outbound.to_excel(writer, sheet_name="全部出库明细", index=False)
    output.seek(0)

    filename = f"仓库数据导出_{date.today().isoformat()}.xlsx"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


# ── 启动入口 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import threading
    import webbrowser

    # 初始化数据库（建表 + 默认管理员 + 清理）
    with app.app_context():
        init_db()

    # 获取本机局域网 IP
    def get_lan_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.254.254.254", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    lan_ip = get_lan_ip()

    # 打印启动信息（兼容 GBK 编码的 Windows 终端）
    try:
        print(f"[OK] 数据库已连接: SQLite → {os.path.join(BASE_DIR, 'data', 'warehouse.db')}")
        print(f"[START] 仓库管理系统启动中...")
        print(f"  本地访问:    http://127.0.0.1:{config.PORT}")
        print(f"  局域网访问:  http://{lan_ip}:{config.PORT}")
        print(f"  线程数:      {config.THREADS}")
    except UnicodeEncodeError:
        sys.stdout.reconfigure(encoding="utf-8")
        print(f"✅ 数据库已连接: SQLite → {os.path.join(BASE_DIR,'data', 'warehouse.db')}")
        print(f"🚀 仓库管理系统启动中...")
        print(f"  本地访问:    http://127.0.0.1:{config.PORT}")
        print(f"  局域网访问:  http://{lan_ip}:{config.PORT}")

    # 自动打开浏览器
    def open_browser():
        import time
        time.sleep(1.5)
        try:
            webbrowser.open_new(f"http://127.0.0.1:{config.PORT}")
        except Exception as e:
            print(f"⚠️ 自动打开浏览器失败，请手动访问 http://127.0.0.1:{config.PORT}  (错误: {e})")

    threading.Timer(1.5, open_browser).start()

    # 启动生产服务器
    serve(app, host=config.HOST, port=config.PORT, threads=config.THREADS)