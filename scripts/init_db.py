"""生成演示数据库。

为什么不用一份现成的 SQL 备份文件？
因为固定随机种子生成，任何人跑出来的数据完全一致，评测结果才可复现；
同时数据规模可以按需调整（把 ORDERS 调小，几秒就能跑完）。

表的数量刻意做成 14 张：够多，才会出现"模型抓错表"这个真实问题；
又够少，一个人能在一周内把每一张表讲清楚。
"""

from __future__ import annotations

import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "demo_shop.db"

SEED = 20260101
N_CUSTOMERS = 2000
N_PRODUCTS = 300
N_ORDERS = 20000
N_SUPPLIERS = 20

START = date(2026, 1, 1)
END = date(2026, 10, 31)

REGION_TREE = [
    ("华东", [("江苏省", ["南京市", "苏州市", "无锡市"]),
              ("浙江省", ["杭州市", "宁波市", "温州市"]),
              ("上海市", ["上海市"])]),
    ("华南", [("广东省", ["广州市", "深圳市", "东莞市"]),
              ("广西壮族自治区", ["南宁市", "桂林市"]),
              ("海南省", ["海口市", "三亚市"])]),
    ("华北", [("北京市", ["北京市"]),
              ("天津市", ["天津市"]),
              ("河北省", ["石家庄市", "唐山市"])]),
    ("西南", [("四川省", ["成都市", "绵阳市"]),
              ("重庆市", ["重庆市"]),
              ("云南省", ["昆明市", "大理市"])]),
]

CATEGORY_TREE = [
    ("数码产品", ["手机", "笔记本电脑", "耳机", "智能手表"]),
    ("家用电器", ["冰箱", "洗衣机", "空调"]),
    ("服饰鞋包", ["运动鞋", "外套", "双肩包"]),
    ("食品饮料", ["咖啡", "坚果", "乳制品"]),
    ("美妆个护", ["面霜", "洗发水", "香水"]),
]

CHANNELS = ["自然搜索", "付费广告", "社交推荐", "线下门店", "老客推荐"]
PAY_METHODS = ["支付宝", "微信支付", "银行卡"]
CARRIERS = ["顺丰速运", "中通快递", "京东物流"]
WAREHOUSES = ["华东一号仓", "华南二号仓"]
REVIEW_TEXTS = [
    "物流很快，包装完好", "质量不错，会回购", "和描述一致", "一般般，没有惊喜",
    "性价比很高", "客服态度好", "尺寸偏小，建议买大一号", "用了两天就出问题了",
]
REFUND_REASONS = ["七天无理由", "商品破损", "发错货", "质量问题", "拍错了"]

# 类目编号规则：先插入 5 个一级类目（1 到 5），再依次插入子类目（6 起）
LEAF_CATEGORY_IDS = list(range(6, 6 + sum(len(children) for _, children in CATEGORY_TREE)))

DDL = [
    ("regions", """
        CREATE TABLE regions (
            id        INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            level     INTEGER NOT NULL,
            parent_id INTEGER REFERENCES regions(id)
        )"""),
    ("customers", """
        CREATE TABLE customers (
            id            INTEGER PRIMARY KEY,
            name          TEXT NOT NULL,
            gender        TEXT,
            birth_date    TEXT,
            register_date TEXT NOT NULL,
            region_id     INTEGER REFERENCES regions(id),
            channel       TEXT
        )"""),
    ("customer_addresses", """
        CREATE TABLE customer_addresses (
            id          INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(id),
            region_id   INTEGER REFERENCES regions(id),
            detail      TEXT,
            is_default  INTEGER
        )"""),
    ("categories", """
        CREATE TABLE categories (
            id        INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            parent_id INTEGER REFERENCES categories(id)
        )"""),
    ("suppliers", """
        CREATE TABLE suppliers (
            id        INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            region_id INTEGER REFERENCES regions(id),
            rating    REAL
        )"""),
    ("products", """
        CREATE TABLE products (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            category_id INTEGER REFERENCES categories(id),
            supplier_id INTEGER REFERENCES suppliers(id),
            price       REAL NOT NULL,
            cost        REAL,
            status      TEXT,
            created_at  TEXT
        )"""),
    ("campaigns", """
        CREATE TABLE campaigns (
            id         INTEGER PRIMARY KEY,
            name       TEXT,
            type       TEXT,
            start_date TEXT,
            end_date   TEXT,
            budget     REAL
        )"""),
    ("orders", """
        CREATE TABLE orders (
            id              INTEGER PRIMARY KEY,
            order_no        TEXT NOT NULL,
            customer_id     INTEGER REFERENCES customers(id),
            order_date      TEXT NOT NULL,
            status          TEXT,
            total_amount    REAL,
            discount_amount REAL,
            pay_amount      REAL,
            campaign_id     INTEGER REFERENCES campaigns(id)
        )"""),
    ("order_items", """
        CREATE TABLE order_items (
            id         INTEGER PRIMARY KEY,
            order_id   INTEGER REFERENCES orders(id),
            product_id INTEGER REFERENCES products(id),
            quantity   INTEGER,
            unit_price REAL,
            amount     REAL
        )"""),
    ("payments", """
        CREATE TABLE payments (
            id      INTEGER PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id),
            method  TEXT,
            amount  REAL,
            paid_at TEXT,
            status  TEXT
        )"""),
    ("refunds", """
        CREATE TABLE refunds (
            id         INTEGER PRIMARY KEY,
            order_id   INTEGER REFERENCES orders(id),
            reason     TEXT,
            amount     REAL,
            created_at TEXT,
            status     TEXT
        )"""),
    ("shipments", """
        CREATE TABLE shipments (
            id           INTEGER PRIMARY KEY,
            order_id     INTEGER REFERENCES orders(id),
            carrier      TEXT,
            tracking_no  TEXT,
            shipped_at   TEXT,
            delivered_at TEXT,
            status       TEXT
        )"""),
    ("reviews", """
        CREATE TABLE reviews (
            id         INTEGER PRIMARY KEY,
            order_id   INTEGER REFERENCES orders(id),
            product_id INTEGER REFERENCES products(id),
            rating     INTEGER,
            content    TEXT,
            created_at TEXT
        )"""),
    ("inventory", """
        CREATE TABLE inventory (
            id         INTEGER PRIMARY KEY,
            product_id INTEGER REFERENCES products(id),
            warehouse  TEXT,
            quantity   INTEGER,
            updated_at TEXT
        )"""),

    # ---------- 内容与投放域 ----------
    # 这一组表与订单域**没有任何外键关联**，是刻意加的"干扰项"。
    # 只有库里有大量无关表，"检索该看哪几张表"才是一个真问题；
    # 如果全库只有十几张互相连着的表，检索闭着眼睛也能全召回，指标就没有意义。
    ("authors", """
        CREATE TABLE authors (
            id   INTEGER PRIMARY KEY,
            name TEXT,
            bio  TEXT
        )"""),
    ("topics", """
        CREATE TABLE topics (
            id        INTEGER PRIMARY KEY,
            name      TEXT,
            parent_id INTEGER REFERENCES topics(id)
        )"""),
    ("articles", """
        CREATE TABLE articles (
            id           INTEGER PRIMARY KEY,
            title        TEXT,
            author_id    INTEGER REFERENCES authors(id),
            topic_id     INTEGER REFERENCES topics(id),
            publish_date TEXT,
            word_count   INTEGER,
            status       TEXT
        )"""),
    ("article_tags", """
        CREATE TABLE article_tags (
            id         INTEGER PRIMARY KEY,
            article_id INTEGER REFERENCES articles(id),
            tag        TEXT
        )"""),
    ("comments", """
        CREATE TABLE comments (
            id         INTEGER PRIMARY KEY,
            article_id INTEGER REFERENCES articles(id),
            user_nick  TEXT,
            content    TEXT,
            rating     INTEGER,
            created_at TEXT
        )"""),
    ("page_views", """
        CREATE TABLE page_views (
            id             INTEGER PRIMARY KEY,
            article_id     INTEGER REFERENCES articles(id),
            view_date      TEXT,
            views          INTEGER,
            avg_duration_s REAL
        )"""),
    ("subscriptions", """
        CREATE TABLE subscriptions (
            id         INTEGER PRIMARY KEY,
            email      TEXT,
            channel    TEXT,
            status     TEXT,
            created_at TEXT
        )"""),
    ("newsletter_sends", """
        CREATE TABLE newsletter_sends (
            id              INTEGER PRIMARY KEY,
            subscription_id INTEGER REFERENCES subscriptions(id),
            article_id      INTEGER REFERENCES articles(id),
            sent_at         TEXT,
            opened          INTEGER,
            clicked         INTEGER
        )"""),
    ("keyword_metrics", """
        CREATE TABLE keyword_metrics (
            id            INTEGER PRIMARY KEY,
            keyword       TEXT,
            month         TEXT,
            search_volume INTEGER,
            click_rate    REAL
        )"""),
    ("ad_spend", """
        CREATE TABLE ad_spend (
            id          INTEGER PRIMARY KEY,
            channel     TEXT,
            month       TEXT,
            spend       REAL,
            impressions INTEGER,
            clicks      INTEGER
        )"""),
]

# 外键列建索引。
# 没有索引时，一条按客户分组的查询要全表扫描几十万次；真实系统里这是最基本的优化，
# 也直接影响"评测跑得完跑不完"——相关子查询在没有索引时会直接超时。
INDEXES = [
    "CREATE INDEX idx_orders_customer ON orders(customer_id)",
    "CREATE INDEX idx_orders_date ON orders(order_date)",
    "CREATE INDEX idx_orders_campaign ON orders(campaign_id)",
    "CREATE INDEX idx_order_items_order ON order_items(order_id)",
    "CREATE INDEX idx_order_items_product ON order_items(product_id)",
    "CREATE INDEX idx_payments_order ON payments(order_id)",
    "CREATE INDEX idx_refunds_order ON refunds(order_id)",
    "CREATE INDEX idx_shipments_order ON shipments(order_id)",
    "CREATE INDEX idx_reviews_product ON reviews(product_id)",
    "CREATE INDEX idx_reviews_order ON reviews(order_id)",
    "CREATE INDEX idx_customers_region ON customers(region_id)",
    "CREATE INDEX idx_products_category ON products(category_id)",
    "CREATE INDEX idx_products_supplier ON products(supplier_id)",
    "CREATE INDEX idx_inventory_product ON inventory(product_id)",
    "CREATE INDEX idx_articles_author ON articles(author_id)",
    "CREATE INDEX idx_articles_topic ON articles(topic_id)",
    "CREATE INDEX idx_comments_article ON comments(article_id)",
    "CREATE INDEX idx_page_views_article ON page_views(article_id)",
    "CREATE INDEX idx_newsletter_article ON newsletter_sends(article_id)",
]

META_DDL = [
    ("_meta_tables", """
        CREATE TABLE _meta_tables (
            table_name  TEXT PRIMARY KEY,
            description TEXT
        )"""),
    ("_meta_columns", """
        CREATE TABLE _meta_columns (
            table_name  TEXT,
            column_name TEXT,
            description TEXT,
            PRIMARY KEY (table_name, column_name)
        )"""),
]

TABLE_META: dict[str, tuple[str, dict[str, str]]] = {
    "regions": ("地区维度表，三级结构：大区 → 省 → 市，用 parent_id 自关联", {
        "id": "地区编号", "name": "地区名称", "level": "层级：1=大区，2=省，3=市",
        "parent_id": "上一级地区的编号",
    }),
    "customers": ("客户表，一行一个注册客户", {
        "id": "客户编号", "name": "客户姓名", "gender": "性别：男/女",
        "birth_date": "出生日期", "register_date": "注册日期",
        "region_id": "所属城市编号，关联 regions.id", "channel": "获客渠道",
    }),
    "customer_addresses": ("收货地址表，一个客户可能有多个地址", {
        "id": "地址编号", "customer_id": "客户编号", "region_id": "所在城市编号",
        "detail": "详细地址", "is_default": "是否默认地址：1=是，0=否",
    }),
    "categories": ("商品类目表，两级结构，parent_id 为空表示一级类目", {
        "id": "类目编号", "name": "类目名称", "parent_id": "上级类目编号",
    }),
    "suppliers": ("供应商表", {
        "id": "供应商编号", "name": "供应商名称", "region_id": "所在城市编号",
        "rating": "综合评分，满分 5 分",
    }),
    "products": ("商品表", {
        "id": "商品编号", "name": "商品名称", "category_id": "所属类目编号",
        "supplier_id": "供应商编号", "price": "售价", "cost": "成本价",
        "status": "状态：在售/下架", "created_at": "上架时间",
    }),
    "campaigns": ("营销活动表", {
        "id": "活动编号", "name": "活动名称", "type": "活动类型：满减/折扣/赠品",
        "start_date": "开始日期", "end_date": "结束日期", "budget": "活动预算",
    }),
    "orders": ("订单主表，一行一个订单。金额口径：total_amount 是原价合计，discount_amount 是优惠金额，pay_amount 是实付金额（原价减优惠）", {
        "id": "订单编号", "order_no": "订单号", "customer_id": "下单客户编号",
        "order_date": "下单日期", "status": "订单状态：已支付/已完成/已取消/已退款",
        "total_amount": "原价合计", "discount_amount": "优惠金额", "pay_amount": "实付金额",
        "campaign_id": "参与的营销活动编号，未参加为空",
    }),
    "order_items": ("订单明细表，一行是订单里的一个商品", {
        "id": "明细编号", "order_id": "所属订单编号", "product_id": "商品编号",
        "quantity": "购买数量", "unit_price": "下单时单价", "amount": "小计金额",
    }),
    "payments": ("支付流水表", {
        "id": "支付编号", "order_id": "订单编号", "method": "支付方式",
        "amount": "支付金额", "paid_at": "支付时间", "status": "支付状态：成功/失败",
    }),
    "refunds": ("退款表", {
        "id": "退款编号", "order_id": "订单编号", "reason": "退款原因",
        "amount": "退款金额", "created_at": "申请时间", "status": "退款状态：已同意/已拒绝/处理中",
    }),
    "shipments": ("物流表", {
        "id": "物流编号", "order_id": "订单编号", "carrier": "承运商",
        "tracking_no": "运单号", "shipped_at": "发货时间", "delivered_at": "签收时间",
        "status": "物流状态：已发货/已签收/运输中",
    }),
    "reviews": ("商品评价表", {
        "id": "评价编号", "order_id": "订单编号", "product_id": "商品编号",
        "rating": "评分，1 到 5 分", "content": "评价内容", "created_at": "评价时间",
    }),
    "inventory": ("库存表，一个商品在每个仓库一条记录", {
        "id": "库存编号", "product_id": "商品编号", "warehouse": "仓库名称",
        "quantity": "可用库存数量", "updated_at": "更新时间",
    }),
    "authors": ("内容作者表", {
        "id": "作者编号", "name": "作者姓名", "bio": "作者简介",
    }),
    "topics": ("内容栏目表，两级结构，parent_id 为空表示一级栏目", {
        "id": "栏目编号", "name": "栏目名称", "parent_id": "上级栏目编号",
    }),
    "articles": ("文章表，与订单域无关", {
        "id": "文章编号", "title": "标题", "author_id": "作者编号", "topic_id": "栏目编号",
        "publish_date": "发布日期", "word_count": "字数", "status": "状态：已发布/草稿",
    }),
    "article_tags": ("文章标签表，一篇文章可以有多个标签", {
        "id": "记录编号", "article_id": "文章编号", "tag": "标签名",
    }),
    "comments": ("文章评论表", {
        "id": "评论编号", "article_id": "文章编号", "user_nick": "评论者昵称",
        "content": "评论内容", "rating": "评分，1 到 5 分", "created_at": "评论时间",
    }),
    "page_views": ("文章每日浏览统计表", {
        "id": "记录编号", "article_id": "文章编号", "view_date": "统计日期",
        "views": "当日浏览量", "avg_duration_s": "平均停留时长（秒）",
    }),
    "subscriptions": ("邮件订阅用户表", {
        "id": "订阅编号", "email": "邮箱", "channel": "订阅渠道",
        "status": "状态：有效/退订", "created_at": "订阅时间",
    }),
    "newsletter_sends": ("邮件推送记录表", {
        "id": "推送编号", "subscription_id": "订阅编号", "article_id": "文章编号",
        "sent_at": "发送时间", "opened": "是否打开：1=是，0=否", "clicked": "是否点击：1=是，0=否",
    }),
    "keyword_metrics": ("关键词月度搜索指标表", {
        "id": "记录编号", "keyword": "关键词", "month": "月份，形如 2026-03",
        "search_volume": "搜索量", "click_rate": "点击率",
    }),
    "ad_spend": ("各渠道月度广告投放表", {
        "id": "记录编号", "channel": "投放渠道", "month": "月份",
        "spend": "花费金额", "impressions": "曝光次数", "clicks": "点击次数",
    }),
}


def _rand_date(rng: random.Random, start: date = START, end: date = END) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build_regions(conn: sqlite3.Connection, rng: random.Random) -> dict[str, int]:
    """建地区树，返回 {城市名: 编号}，供后面随机分配客户地区用。"""
    city_ids: dict[str, int] = {}
    rid = 0
    rows = []
    for big_name, provinces in REGION_TREE:
        rid += 1
        big_id = rid
        rows.append((rid, big_name, 1, None))
        for prov_name, cities in provinces:
            rid += 1
            prov_id = rid
            rows.append((rid, prov_name, 2, big_id))
            for city_name in cities:
                rid += 1
                rows.append((rid, city_name, 3, prov_id))
                city_ids[city_name] = rid
    conn.executemany("INSERT INTO regions VALUES (?, ?, ?, ?)", rows)
    return city_ids


def build_customers(conn, rng, city_ids) -> list[tuple]:
    rows = []
    city_list = list(city_ids.values())
    for i in range(1, N_CUSTOMERS + 1):
        reg = _rand_date(rng, date(1970, 1, 1), date(2005, 12, 31))
        signup = _rand_date(rng, date(2024, 1, 1), END)
        rows.append((
            i, f"客户{i:05d}", rng.choice(["男", "女"]), reg.isoformat(),
            signup.isoformat(), rng.choice(city_list), rng.choice(CHANNELS),
        ))
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return rows


def build_categories(conn) -> None:
    rows = []
    cid = 0
    for parent, children in CATEGORY_TREE:
        cid += 1
        rows.append((cid, parent, None))
        parent_id = cid
        for child in children:
            cid += 1
            rows.append((cid, child, parent_id))
    conn.executemany("INSERT INTO categories VALUES (?, ?, ?)", rows)


def build_suppliers(conn, rng, city_ids) -> None:
    city_list = list(city_ids.values())
    rows = [
        (i, f"供应商{i:02d}", rng.choice(city_list), round(rng.uniform(3.0, 5.0), 1))
        for i in range(1, N_SUPPLIERS + 1)
    ]
    conn.executemany("INSERT INTO suppliers VALUES (?, ?, ?, ?)", rows)


def build_products(conn, rng) -> list[float]:
    prices: list[float] = []
    rows = []
    for i in range(1, N_PRODUCTS + 1):
        price = round(rng.uniform(20, 2000), 2)
        prices.append(price)
        rows.append((
            i, f"商品{i:03d}", rng.choice(LEAF_CATEGORY_IDS),
            rng.randint(1, N_SUPPLIERS), price, round(price * 0.6, 2),
            "在售" if rng.random() < 0.9 else "下架",
            _rand_date(rng, date(2025, 1, 1), date(2026, 6, 30)).isoformat(),
        ))
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return prices


def build_campaigns(conn, rng) -> list[tuple[int, date, date]]:
    rows = []
    windows = []
    for i in range(1, 9):
        start = _rand_date(rng, date(2026, 1, 1), date(2026, 9, 1))
        end = start + timedelta(days=rng.choice([3, 7, 15, 30]))
        windows.append((i, start, end))
        rows.append((
            i, f"活动{i:02d}", rng.choice(["满减", "折扣", "赠品"]),
            start.isoformat(), end.isoformat(), round(rng.uniform(10000, 200000), 2),
        ))
    conn.executemany("INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?)", rows)
    return windows


def build_orders(conn, rng, prices, campaign_windows):
    """订单、明细、支付、退款、物流、评价一起生成，保证互相自洽。"""
    statuses = ["已支付", "已完成", "已取消", "已退款"]
    weights = [0.40, 0.45, 0.08, 0.07]

    orders, items, payments, refunds, shipments, reviews = [], [], [], [], [], []
    item_id = 0
    for oid in range(1, N_ORDERS + 1):
        order_date = _rand_date(rng)
        status = rng.choices(statuses, weights=weights, k=1)[0]
        n_items = rng.randint(1, 4)

        total = 0.0
        order_pids: list[int] = []
        for _ in range(n_items):
            pid = rng.randint(1, N_PRODUCTS)
            order_pids.append(pid)
            qty = rng.randint(1, 3)
            unit = prices[pid - 1]
            amount = round(unit * qty, 2)
            total += amount
            item_id += 1
            items.append((item_id, oid, pid, qty, unit, amount))

        discount = round(total * rng.uniform(0, 0.15), 2) if rng.random() < 0.6 else 0.0
        pay = round(total - discount, 2)

        campaign_id = None
        if rng.random() < 0.3:
            active = [cid for cid, s, e in campaign_windows if s <= order_date <= e]
            if active:
                campaign_id = rng.choice(active)

        orders.append((
            oid, f"SO{order_date.strftime('%Y%m%d')}{oid:06d}",
            rng.randint(1, N_CUSTOMERS), order_date.isoformat(), status,
            round(total, 2), discount, pay, campaign_id,
        ))

        if status in ("已支付", "已完成", "已退款"):
            payments.append((
                len(payments) + 1, oid, rng.choice(PAY_METHODS), pay,
                f"{order_date.isoformat()} {rng.randint(8, 23):02d}:{rng.randint(0, 59):02d}:00",
                "成功",
            ))
            if status == "已退款":
                refunds.append((
                    len(refunds) + 1, oid, rng.choice(REFUND_REASONS), pay,
                    f"{order_date.isoformat()} 10:00:00", rng.choice(["已同意", "处理中"]),
                ))
        if status in ("已支付", "已完成") and rng.random() < 0.85:
            shipped = order_date + timedelta(days=rng.randint(0, 2))
            delivered = shipped + timedelta(days=rng.randint(1, 5))
            shipments.append((
                len(shipments) + 1, oid, rng.choice(CARRIERS),
                f"SF{rng.randint(10**11, 10**12 - 1)}",
                shipped.isoformat(), delivered.isoformat(),
                "已签收" if status == "已完成" else "运输中",
            ))
        if status == "已完成" and rng.random() < 0.4:
            reviews.append((
                len(reviews) + 1, oid, rng.choice(order_pids),
                rng.choices([1, 2, 3, 4, 5], weights=[3, 4, 10, 30, 53], k=1)[0],
                rng.choice(REVIEW_TEXTS),
                (order_date + timedelta(days=rng.randint(1, 10))).isoformat(),
            ))

    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", items)
    conn.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)", payments)
    conn.executemany("INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)", refunds)
    conn.executemany("INSERT INTO shipments VALUES (?, ?, ?, ?, ?, ?, ?)", shipments)
    conn.executemany("INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?)", reviews)


def build_addresses(conn, rng, city_ids) -> None:
    rows = []
    city_list = list(city_ids.values())
    for cid in range(1, N_CUSTOMERS + 1):
        n = rng.randint(1, 2)
        for j in range(n):
            rows.append((len(rows) + 1, cid, rng.choice(city_list),
                         f"{rng.randint(1, 200)}号", 1 if j == 0 else 0))
    conn.executemany("INSERT INTO customer_addresses VALUES (?, ?, ?, ?, ?)", rows)


def build_inventory(conn, rng) -> None:
    rows = []
    for pid in range(1, N_PRODUCTS + 1):
        for wh in WAREHOUSES:
            rows.append((len(rows) + 1, pid, wh, rng.randint(0, 500), END.isoformat()))
    conn.executemany("INSERT INTO inventory VALUES (?, ?, ?, ?, ?)", rows)


TOPIC_NAMES = ["人工智能", "数据分析", "增长运营", "内容营销", "产品设计", "用户研究"]
SUBTITLES = ["入门指南", "实战复盘", "的五个误区", "如何提升转化", "月度观察"]
TAG_POOL = ["RAG", "微调", "提示词", "指标", "复盘", "案例", "工具", "方法论"]
NICK_POOL = ["路人甲", "数据小王", "运营喵", "产品老李", "学习者", "阿May"]
COMMENT_POOL = [
    "写得清楚，收藏了", "这块能再展开讲讲吗", "和我们团队的实践一致",
    "例子有点少", "看完还是不太懂", "求完整代码", "观点有点绝对",
]
SUB_CHANNELS = ["官网", "公众号", "线下活动", "合作渠道"]
AD_CHANNELS = ["信息流", "搜索", "社交", "电商站内"]


def build_content_domain(conn, rng) -> None:
    """内容与投放域：与订单域完全没有外键关联，专门用来给检索制造干扰。"""
    # 作者
    authors = [(i, f"作者{i:02d}", f"{rng.choice(TOPIC_NAMES)}方向写作者")
               for i in range(1, 31)]
    conn.executemany("INSERT INTO authors VALUES (?, ?, ?)", authors)

    # 栏目：6 个一级，每个 2 个二级
    topics = []
    tid = 0
    leaf_topic_ids = []
    for name in TOPIC_NAMES:
        tid += 1
        topics.append((tid, name, None))
        parent = tid
        for suffix in ("基础", "进阶"):
            tid += 1
            topics.append((tid, f"{name}{suffix}", parent))
            leaf_topic_ids.append(tid)
    conn.executemany("INSERT INTO topics VALUES (?, ?, ?)", topics)

    # 文章
    articles = []
    for i in range(1, 801):
        topic_id = rng.choice(leaf_topic_ids)
        topic_name = next(t[1] for t in topics if t[0] == topic_id)
        title = f"{topic_name}{rng.choice(SUBTITLES)}"
        articles.append((
            i, f"{title}（{i}）", rng.randint(1, 30), topic_id,
            _rand_date(rng, date(2025, 1, 1), END).isoformat(),
            rng.randint(800, 4000),
            "已发布" if rng.random() < 0.9 else "草稿",
        ))
    conn.executemany("INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)", articles)

    # 标签
    tags = []
    for article_id in range(1, 801):
        for tag in rng.sample(TAG_POOL, rng.randint(2, 3)):
            tags.append((len(tags) + 1, article_id, tag))
    conn.executemany("INSERT INTO article_tags VALUES (?, ?, ?)", tags)

    # 评论
    comments = []
    for article_id in range(1, 801):
        for _ in range(rng.randint(1, 6)):
            comments.append((
                len(comments) + 1, article_id, rng.choice(NICK_POOL),
                rng.choice(COMMENT_POOL),
                rng.choices([1, 2, 3, 4, 5], weights=[2, 4, 12, 35, 47], k=1)[0],
                _rand_date(rng, date(2025, 6, 1), END).isoformat(),
            ))
    conn.executemany("INSERT INTO comments VALUES (?, ?, ?, ?, ?, ?)", comments)

    # 浏览统计
    views = []
    for article_id in range(1, 801):
        for _ in range(rng.randint(3, 8)):
            views.append((
                len(views) + 1, article_id,
                _rand_date(rng, date(2026, 8, 1), END).isoformat(),
                rng.randint(50, 5000),
                round(rng.uniform(20, 300), 1),
            ))
    conn.executemany("INSERT INTO page_views VALUES (?, ?, ?, ?, ?)", views)

    # 订阅
    subs = [(
        i, f"user{i:05d}@example.com", rng.choice(SUB_CHANNELS),
        "有效" if rng.random() < 0.85 else "退订",
        _rand_date(rng, date(2024, 1, 1), END).isoformat(),
    ) for i in range(1, 1501)]
    conn.executemany("INSERT INTO subscriptions VALUES (?, ?, ?, ?, ?)", subs)

    # 推送记录
    sends = [(
        i, rng.randint(1, 1500), rng.randint(1, 800),
        _rand_date(rng, date(2026, 1, 1), END).isoformat(),
        1 if rng.random() < 0.35 else 0,
        1 if rng.random() < 0.12 else 0,
    ) for i in range(1, 2001)]
    conn.executemany("INSERT INTO newsletter_sends VALUES (?, ?, ?, ?, ?, ?)", sends)

    # 关键词指标：20 个词 × 10 个月
    keywords = ["数据问答", "文本转查询", "智能报表", "指标口径", "数据治理", "用户增长",
                "内容营销", "转化率", "留存分析", "渠道投放", "自然流量", "付费流量",
                "客单价", "复购", "退货率", "库存周转", "供应链", "会员体系",
                "私域运营", "活动策划"]
    months = [f"2026-{m:02d}" for m in range(1, 11)]
    metrics = [(
        len(keywords) * mi + ki + 1, kw, month,
        rng.randint(500, 50000), round(rng.uniform(0.01, 0.2), 4),
    ) for mi, month in enumerate(months) for ki, kw in enumerate(keywords)]
    conn.executemany("INSERT INTO keyword_metrics VALUES (?, ?, ?, ?, ?)", metrics)

    # 广告投放：4 渠道 × 10 个月
    spend = [(
        len(AD_CHANNELS) * mi + ci + 1, channel, month,
        round(rng.uniform(10000, 300000), 2),
        rng.randint(50000, 2000000),
        rng.randint(500, 50000),
    ) for mi, month in enumerate(months) for ci, channel in enumerate(AD_CHANNELS)]
    conn.executemany("INSERT INTO ad_spend VALUES (?, ?, ?, ?, ?, ?)", spend)


def write_meta(conn) -> None:
    table_rows = [(name, desc) for name, (desc, _) in TABLE_META.items()]
    conn.executemany("INSERT INTO _meta_tables VALUES (?, ?)", table_rows)
    col_rows = [
        (table, column, desc)
        for table, (_, columns) in TABLE_META.items()
        for column, desc in columns.items()
    ]
    conn.executemany("INSERT INTO _meta_columns VALUES (?, ?, ?)", col_rows)


def build(db_path: Path = DEFAULT_DB) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rng = random.Random(SEED)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for _, sql in DDL + META_DDL:
            conn.execute(sql)
        for statement in INDEXES:
            conn.execute(statement)

        city_ids = build_regions(conn, rng)
        build_customers(conn, rng, city_ids)
        build_addresses(conn, rng, city_ids)
        build_categories(conn)
        build_suppliers(conn, rng, city_ids)
        prices = build_products(conn, rng)
        windows = build_campaigns(conn, rng)
        build_orders(conn, rng, prices, windows)
        build_inventory(conn, rng)
        build_content_domain(conn, rng)
        write_meta(conn)

        conn.commit()
    finally:
        conn.close()
    return db_path


def summarize(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_meta\\_%' ESCAPE '\\' "
            "ORDER BY name"
        ).fetchall()
        print(f"数据库已生成：{db_path}")
        total = 0
        for (name,) in rows:
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            total += count
            print(f"  {name:<20}{count:>8} 行")
        print(f"  {'合计':<20}{total:>8} 行")
    finally:
        conn.close()


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    db = build(path)
    summarize(db)


if __name__ == "__main__":
    main()
