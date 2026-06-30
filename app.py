#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
二房东门面租赁账期催缴系统 — 网页版
"""

import sqlite3
import os
import threading
import webbrowser
from datetime import datetime, date, timedelta

import requests
from flask import Flask, render_template, request, redirect, url_for, flash

# ─── 配置 ────────────────────────────────────
DB_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "lease_data.db")
DATE_FMT = "%Y-%m-%d"

# ⚠️ 请在这里填写你的 Server酱 SendKey
# 获取地址: https://sct.ftqq.com
SCKEY = "请替换为你的Server酱KEY"
SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"

# ─── Flask ───────────────────────────────────
app = Flask(__name__)
app.secret_key = os.urandom(16).hex()


# ─── Jinja2 自定义过滤器 ───────────────────
@app.template_filter("to_date")
def to_date_filter(date_str):
    return datetime.strptime(date_str, DATE_FMT).date()


# ============================================================
#  数据库工具
# ============================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Shops (
            shop_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_name  TEXT NOT NULL,
            address    TEXT DEFAULT ''
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS LandlordContracts (
            contract_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id            INTEGER NOT NULL,
            landlord_name      TEXT NOT NULL,
            landlord_phone     TEXT DEFAULT '',
            signing_date       TEXT DEFAULT '',
            annual_amount      REAL DEFAULT 0,
            end_date           TEXT DEFAULT '',
            next_payment_date  TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS TenantContracts (
            contract_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id            INTEGER NOT NULL,
            tenant_name        TEXT NOT NULL,
            tenant_phone       TEXT NOT NULL,
            signing_date       TEXT DEFAULT '',
            annual_amount      REAL DEFAULT 0,
            end_date           TEXT DEFAULT '',
            next_payment_date  TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


# ============================================================
#  SQL 日期辅助
# ============================================================

def sql_now():
    return date.today().isoformat()

def sql_now_plus(days):
    return (date.today() + timedelta(days=days)).isoformat()


# ============================================================
#  Server酱 微信推送
# ============================================================

def send_wechat(title: str, content: str) -> bool:
    key = app.config.get("SCKEY", SCKEY)
    if not key or key == "请替换为你的Server酱KEY":
        return False
    url = SERVERCHAN_URL.format(key=key)
    try:
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
        return resp.json().get("code") == 0
    except Exception:
        return False


# ============================================================
#  每日检查逻辑（提前7天和3天提醒）
# ============================================================

def run_daily_check() -> list:
    today = date.today()
    messages = []
    conn = get_conn()

    for days_before in [7, 3]:
        target = today + timedelta(days=days_before)
        ds = str(days_before)

        # ── 租户催款 ──
        rows = conn.execute("""
            SELECT s.shop_name, t.tenant_name, t.tenant_phone, t.next_payment_date
            FROM TenantContracts t
            JOIN Shops s ON s.shop_id = t.shop_id
            WHERE t.next_payment_date = ?
        """, (target.isoformat(),)).fetchall()

        for row in rows:
            pd = datetime.strptime(row["next_payment_date"], DATE_FMT).date()
            prefix = "⚠️ 即将交租" if days_before == 7 else "🚨 催款预警"
            msg = (
                f"【{prefix}】{row['shop_name']}的租户[{row['tenant_name']}]"
                f"应于 {ds} 天后（{pd.month}月{pd.day}日）交租，"
                f"请及时联系催缴。\n联系电话：{row['tenant_phone']}。"
            )
            messages.append({"type": "tenant", "days": days_before, "text": msg})
            sent = send_wechat("💰 租户催款提醒", msg)
            if sent:
                messages[-1]["pushed"] = True

        # ── 房东付款 ──
        rows = conn.execute("""
            SELECT s.shop_name, l.landlord_name, l.landlord_phone, l.next_payment_date
            FROM LandlordContracts l
            JOIN Shops s ON s.shop_id = l.shop_id
            WHERE l.next_payment_date = ?
        """, (target.isoformat(),)).fetchall()

        for row in rows:
            pd = datetime.strptime(row["next_payment_date"], DATE_FMT).date()
            prefix = "⚠️ 即将付款" if days_before == 7 else "🚨 付款预警"
            phone = row["landlord_phone"] or ""
            phone_part = f"\n房东电话：{phone}。" if phone else "。"
            msg = (
                f"【{prefix}】{row['shop_name']}的房东[{row['landlord_name']}]"
                f"的租金还有 {ds} 天到期（{pd.month}月{pd.day}日），"
                f"请及时安排打款{phone_part}"
            )
            messages.append({"type": "landlord", "days": days_before, "text": msg})
            sent = send_wechat("🏦 房东付款提醒", msg)
            if sent:
                messages[-1]["pushed"] = True

    conn.close()
    return messages


# ============================================================
#  首页 — 仪表盘
# ============================================================

@app.route("/")
def index():
    conn = get_conn()
    today = date.today()
    tn = sql_now()
    tn30 = sql_now_plus(30)

    upcoming_tenants = conn.execute("""
        SELECT s.shop_name, s.address, t.tenant_name, t.tenant_phone, t.annual_amount, t.next_payment_date
        FROM TenantContracts t
        JOIN Shops s ON s.shop_id = t.shop_id
        WHERE t.next_payment_date >= ?
          AND t.next_payment_date <= ?
        ORDER BY t.next_payment_date
    """, (tn, tn30)).fetchall()

    upcoming_landlords = conn.execute("""
        SELECT s.shop_name, s.address, l.landlord_name, l.landlord_phone, l.annual_amount, l.next_payment_date
        FROM LandlordContracts l
        JOIN Shops s ON s.shop_id = l.shop_id
        WHERE l.next_payment_date >= ?
          AND l.next_payment_date <= ?
        ORDER BY l.next_payment_date
    """, (tn, tn30)).fetchall()

    shop_count = conn.execute("SELECT COUNT(*) AS c FROM Shops").fetchone()["c"]
    tenant_count = conn.execute("SELECT COUNT(*) AS c FROM TenantContracts").fetchone()["c"]
    landlord_count = conn.execute("SELECT COUNT(*) AS c FROM LandlordContracts").fetchone()["c"]

    conn.close()
    return render_template("index.html",
        today=today,
        upcoming_tenants=upcoming_tenants,
        upcoming_landlords=upcoming_landlords,
        shop_count=shop_count,
        tenant_count=tenant_count,
        landlord_count=landlord_count,
        key_ok=app.config.get("SCKEY", SCKEY) != "请替换为你的Server酱KEY",
    )


# ============================================================
#  门面管理
# ============================================================

@app.route("/shops", methods=["GET", "POST"])
def shops():
    conn = get_conn()
    if request.method == "POST":
        name = request.form.get("shop_name", "").strip()
        address = request.form.get("address", "").strip()
        if name:
            conn.execute("INSERT INTO Shops (shop_name, address) VALUES (?, ?)", (name, address))
            conn.commit()
            flash(f"门面「{name}」已添加", "success")
        else:
            flash("请输入门面名称", "error")
        conn.close()
        return redirect(url_for("shops"))

    all_shops = conn.execute("""
        SELECT s.*,
            (SELECT COUNT(*) FROM LandlordContracts l WHERE l.shop_id = s.shop_id) AS landlord_count,
            (SELECT COUNT(*) FROM TenantContracts t WHERE t.shop_id = s.shop_id) AS tenant_count
        FROM Shops s ORDER BY s.shop_id
    """).fetchall()
    conn.close()
    return render_template("shops.html", shops=all_shops)


@app.route("/shops/<int:shop_id>/delete", methods=["POST"])
def delete_shop(shop_id):
    conn = get_conn()
    conn.execute("DELETE FROM LandlordContracts WHERE shop_id = ?", (shop_id,))
    conn.execute("DELETE FROM TenantContracts WHERE shop_id = ?", (shop_id,))
    conn.execute("DELETE FROM Shops WHERE shop_id = ?", (shop_id,))
    conn.commit()
    conn.close()
    flash("门面及关联合同已删除", "success")
    return redirect(url_for("shops"))


# ============================================================
#  收楼合同（房东）管理
# ============================================================

@app.route("/landlords", methods=["GET", "POST"])
def landlords():
    conn = get_conn()
    if request.method == "POST":
        shop_id = request.form.get("shop_id", "").strip()
        name = request.form.get("landlord_name", "").strip()
        phone = request.form.get("landlord_phone", "").strip()
        signing = request.form.get("signing_date", "").strip()
        amount = request.form.get("annual_amount", "").strip()
        end = request.form.get("end_date", "").strip()
        date_str = request.form.get("next_payment_date", "").strip()
        if shop_id and name and date_str:
            try:
                datetime.strptime(date_str, DATE_FMT)
                amt = float(amount) if amount else 0
                conn.execute(
                    "INSERT INTO LandlordContracts (shop_id, landlord_name, landlord_phone, signing_date, annual_amount, end_date, next_payment_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (shop_id, name, phone, signing, amt, end, date_str),
                )
                conn.commit()
                flash(f"房东合同「{name}」添加成功", "success")
            except ValueError:
                flash("日期格式错误", "error")
        else:
            flash("请填写完整信息", "error")
        conn.close()
        return redirect(url_for("landlords"))

    contracts = conn.execute("""
        SELECT l.*, s.shop_name
        FROM LandlordContracts l
        JOIN Shops s ON s.shop_id = l.shop_id
        ORDER BY l.next_payment_date
    """).fetchall()
    all_shops = conn.execute("SELECT * FROM Shops ORDER BY shop_id").fetchall()
    conn.close()
    return render_template("landlords.html", contracts=contracts, shops=all_shops)


@app.route("/landlords/<int:cid>/delete", methods=["POST"])
def delete_landlord(cid):
    conn = get_conn()
    conn.execute("DELETE FROM LandlordContracts WHERE contract_id = ?", (cid,))
    conn.commit()
    conn.close()
    flash("房东合同已删除", "success")
    return redirect(url_for("landlords"))


@app.route("/landlords/<int:cid>/edit", methods=["POST"])
def edit_landlord(cid):
    conn = get_conn()
    contract = conn.execute("SELECT * FROM LandlordContracts WHERE contract_id = ?", (cid,)).fetchone()
    if not contract:
        conn.close()
        flash("合同不存在", "error")
        return redirect(url_for("landlords"))
    shop_id = request.form.get("shop_id", "").strip()
    name = request.form.get("landlord_name", "").strip()
    phone = request.form.get("landlord_phone", "").strip()
    signing = request.form.get("signing_date", "").strip()
    amount = request.form.get("annual_amount", "").strip()
    end = request.form.get("end_date", "").strip()
    date_str = request.form.get("next_payment_date", "").strip()
    if shop_id and name and date_str:
        try:
            datetime.strptime(date_str, DATE_FMT)
            amt = float(amount) if amount else 0
            conn.execute(
                "UPDATE LandlordContracts SET shop_id=?, landlord_name=?, landlord_phone=?, signing_date=?, annual_amount=?, end_date=?, next_payment_date=? WHERE contract_id=?",
                (shop_id, name, phone, signing, amt, end, date_str, cid),
            )
            conn.commit()
            flash(f"房东合同已更新", "success")
        except ValueError:
            flash("日期格式错误", "error")
    else:
        flash("请填写完整信息", "error")
    conn.close()
    return redirect(url_for("landlords"))


# ============================================================
#  出楼合同（租户）管理
# ============================================================

@app.route("/tenants", methods=["GET", "POST"])
def tenants():
    conn = get_conn()
    if request.method == "POST":
        shop_id = request.form.get("shop_id", "").strip()
        name = request.form.get("tenant_name", "").strip()
        phone = request.form.get("tenant_phone", "").strip()
        signing = request.form.get("signing_date", "").strip()
        amount = request.form.get("annual_amount", "").strip()
        end = request.form.get("end_date", "").strip()
        date_str = request.form.get("next_payment_date", "").strip()
        if shop_id and name and phone and date_str:
            try:
                datetime.strptime(date_str, DATE_FMT)
                amt = float(amount) if amount else 0
                conn.execute(
                    "INSERT INTO TenantContracts (shop_id, tenant_name, tenant_phone, signing_date, annual_amount, end_date, next_payment_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (shop_id, name, phone, signing, amt, end, date_str),
                )
                conn.commit()
                flash(f"租户合同「{name}」添加成功", "success")
            except ValueError:
                flash("日期格式错误", "error")
        else:
            flash("请填写完整信息", "error")
        conn.close()
        return redirect(url_for("tenants"))

    contracts = conn.execute("""
        SELECT t.*, s.shop_name
        FROM TenantContracts t
        JOIN Shops s ON s.shop_id = t.shop_id
        ORDER BY t.next_payment_date
    """).fetchall()
    all_shops = conn.execute("SELECT * FROM Shops ORDER BY shop_id").fetchall()
    conn.close()
    return render_template("tenants.html", contracts=contracts, shops=all_shops)


@app.route("/tenants/<int:cid>/delete", methods=["POST"])
def delete_tenant(cid):
    conn = get_conn()
    conn.execute("DELETE FROM TenantContracts WHERE contract_id = ?", (cid,))
    conn.commit()
    conn.close()
    flash("租户合同已删除", "success")
    return redirect(url_for("tenants"))


@app.route("/tenants/<int:cid>/edit", methods=["POST"])
def edit_tenant(cid):
    conn = get_conn()
    contract = conn.execute("SELECT * FROM TenantContracts WHERE contract_id = ?", (cid,)).fetchone()
    if not contract:
        conn.close()
        flash("合同不存在", "error")
        return redirect(url_for("tenants"))
    shop_id = request.form.get("shop_id", "").strip()
    name = request.form.get("tenant_name", "").strip()
    phone = request.form.get("tenant_phone", "").strip()
    signing = request.form.get("signing_date", "").strip()
    amount = request.form.get("annual_amount", "").strip()
    end = request.form.get("end_date", "").strip()
    date_str = request.form.get("next_payment_date", "").strip()
    if shop_id and name and phone and date_str:
        try:
            datetime.strptime(date_str, DATE_FMT)
            amt = float(amount) if amount else 0
            conn.execute(
                "UPDATE TenantContracts SET shop_id=?, tenant_name=?, tenant_phone=?, signing_date=?, annual_amount=?, end_date=?, next_payment_date=? WHERE contract_id=?",
                (shop_id, name, phone, signing, amt, end, date_str, cid),
            )
            conn.commit()
            flash(f"租户合同已更新", "success")
        except ValueError:
            flash("日期格式错误", "error")
    else:
        flash("请填写完整信息", "error")
    conn.close()
    return redirect(url_for("tenants"))


# ============================================================
#  手动执行检查
# ============================================================

@app.route("/check", methods=["POST"])
def check_now():
    messages = run_daily_check()
    if not messages:
        flash("今日无需提醒 ✅", "info")
    else:
        for m in messages:
            status = "✅ 已推送微信" if m.get("pushed") else "⚠️ 未配置微信推送"
            flash(f"{m['text']} —— {status}", "info")
    return redirect(url_for("index"))


# ============================================================
#  系统设置（Server酱 Key）
# ============================================================

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        new_key = request.form.get("sckey", "").strip()
        if new_key:
            app.config["SCKEY"] = new_key
            key_file = os.path.join(DB_DIR, ".sckey")
            with open(key_file, "w") as f:
                f.write(new_key)
            flash("Server酱 Key 已保存 ✅", "success")
        else:
            flash("请输入 Key", "error")
        return redirect(url_for("settings"))

    current = app.config.get("SCKEY", SCKEY)
    return render_template("settings.html", sckey=current)


# ============================================================
#  插入测试数据
# ============================================================

def seed_demo_data():
    conn = get_conn()
    try:
        existing = conn.execute("SELECT COUNT(*) AS c FROM Shops").fetchone()["c"]
        if existing > 0:
            return

        # 门面
        conn.execute(
            "INSERT INTO Shops (shop_name, address) VALUES (?, ?)",
            ("绿岛苑301公寓", "绿岛苑301公寓")
        )

        # 房东：王建国，年付10万，每年5月1日付款
        conn.execute(
            "INSERT INTO LandlordContracts (shop_id, landlord_name, landlord_phone, signing_date, annual_amount, end_date, next_payment_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "王建国", "123456789", "2022-05-01", 100000, "2032-05-01", "2027-05-01")
        )

        # 租户：邓宇明，年付15万，每年10月13日付款
        conn.execute(
            "INSERT INTO TenantContracts (shop_id, tenant_name, tenant_phone, signing_date, annual_amount, end_date, next_payment_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "邓宇明", "111222333444", "2022-10-13", 150000, "2032-05-01", "2026-10-13")
        )

        conn.commit()
        print("测试数据已插入")
    finally:
        conn.close()


# ============================================================
#  启动
# ============================================================

def load_sckey():
    key_file = os.path.join(DB_DIR, ".sckey")
    if os.path.exists(key_file):
        with open(key_file) as f:
            saved = f.read().strip()
            if saved:
                app.config["SCKEY"] = saved
                return True
    return False


def main():
    init_db()
    seed_demo_data()
    load_sckey()

    port = int(os.environ.get("PORT", 5000))

    print(f"""
╔══════════════════════════════════════╗
║   二房东门面租赁账期催缴系统          ║
║   浏览器打开: http://0.0.0.0:{port}    ║
╚══════════════════════════════════════╝
""")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)



def init_app():
    """gunicorn 导入时调用的初始化"""
    try:
        init_db()
        seed_demo_data()
        load_sckey()
    except Exception as e:
        print(f"初始化警告: {e}")


# 当 gunicorn 导入时（生产环境）初始化数据库
init_app()

if __name__ == "__main__":
    main()
