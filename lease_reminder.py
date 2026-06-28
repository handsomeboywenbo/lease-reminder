#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
二房东门面租赁账期催缴系统
SQLite + Server酱 / PushDeer 微信推送

用法:
  查看帮助:        python lease_reminder.py --help
  初始化数据库:    python lease_reminder.py --init
  添加数据:        python lease_reminder.py --add
  执行每日检查:    python lease_reminder.py
  建议配合 crontab 每天定时运行:  0 9 * * * cd /path && python lease_reminder.py
"""

import sqlite3
import sys
import os
from datetime import datetime, date, timedelta

import requests

# ─────────────────────────────────────────────
# ⚠️  请在这里填写你的 Server酱 或 PushDeer key
# Server酱：https://sct.ftqq.com   →  SendKey
# PushDeer：https://pushdeer.com   →  PushKey
# ─────────────────────────────────────────────
SCKEY = "请替换为你的Server酱KEY"

# Server酱 API 地址（SendKey 模式）
# 如果使用 PushDeer，请改为 PUSHDEER_URL
SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"

# ─── 数据库路径 ──────────────────────────────
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "lease_data.db")

DATE_FMT = "%Y-%m-%d"


# ============================================================
#  数据库初始化
# ============================================================

def get_conn():
    """获取数据库连接（自动创建目录）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """创建三张基础表（如不存在）"""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS Shops (
            shop_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_name  TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS LandlordContracts (
            contract_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id            INTEGER NOT NULL,
            landlord_name      TEXT NOT NULL,
            next_payment_date  TEXT NOT NULL,
            FOREIGN KEY (shop_id) REFERENCES Shops(shop_id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS TenantContracts (
            contract_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id            INTEGER NOT NULL,
            tenant_name        TEXT NOT NULL,
            tenant_phone       TEXT NOT NULL,
            next_payment_date  TEXT NOT NULL,
            FOREIGN KEY (shop_id) REFERENCES Shops(shop_id)
        );
    """)

    conn.commit()
    conn.close()
    print(f"✅ 数据库初始化完成: {DB_PATH}")


# ============================================================
#  Server酱 / PushDeer 微信推送
# ============================================================

def send_wechat(title: str, content: str) -> bool:
    """
    通过 Server酱 发送微信消息。

    参数:
        title:   消息标题（最长 32 字）
        content: 消息正文（支持 Markdown）

    返回:
        True 发送成功 / False 失败
    """
    if SCKEY == "请替换为你的Server酱KEY":
        print("⚠️  请先在脚本顶部 SCKEY 变量中填写你的 Server酱 Key")
        print("   Server酱: https://sct.ftqq.com")
        print("   PushDeer: https://pushdeer.com")
        return False

    url = SERVERCHAN_URL.format(key=SCKEY)
    payload = {"title": title, "desp": content}

    try:
        resp = requests.post(url, data=payload, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            print(f"✅ 微信推送成功: {title}")
            return True
        else:
            print(f"❌ 微信推送失败: {data.get('message', resp.text)}")
            return False
    except requests.RequestException as e:
        print(f"❌ 网络请求异常: {e}")
        return False


# ============================================================
#  核心催缴逻辑
# ============================================================

def run_daily_check():
    """
    每日检查逻辑:
      每个账期在提前 7 天和提前 3 天各提醒一次
    """
    today = date.today()
    print(f"\n📅 当前日期: {today.isoformat()}")
    print("─" * 40)

    conn = get_conn()
    any_found = False

    for days_before in [7, 3]:
        target = today + timedelta(days=days_before)
        ds = str(days_before)
        tag = "⚠️ 即将" if days_before == 7 else "🚨 紧急"

        # ── 租户催款 ──
        rows = conn.execute("""
            SELECT s.shop_name, t.tenant_name, t.tenant_phone, t.next_payment_date
            FROM TenantContracts t
            JOIN Shops s ON s.shop_id = t.shop_id
            WHERE t.next_payment_date = ?
        """, (target.isoformat(),)).fetchall()

        for row in rows:
            any_found = True
            pd = datetime.strptime(row["next_payment_date"], DATE_FMT).date()
            content = (
                f"【{tag}催款】{row['shop_name']}的租户[{row['tenant_name']}]"
                f"应于 {ds} 天后（{pd.month}月{pd.day}日）交租。"
                f"\n联系电话：{row['tenant_phone']}。"
            )
            send_wechat("💰 租户催款提醒", content)
            print(content + "\n")

        # ── 房东付款 ──
        rows = conn.execute("""
            SELECT s.shop_name, l.landlord_name, l.landlord_phone, l.next_payment_date
            FROM LandlordContracts l
            JOIN Shops s ON s.shop_id = l.shop_id
            WHERE l.next_payment_date = ?
        """, (target.isoformat(),)).fetchall()

        for row in rows:
            any_found = True
            pd = datetime.strptime(row["next_payment_date"], DATE_FMT).date()
            phone = row["landlord_phone"] or ""
            phone_part = f"\n房东电话：{phone}。" if phone else "。"
            content = (
                f"【{tag}付款】{row['shop_name']}的房东[{row['landlord_name']}]"
                f"的租金还有 {ds} 天到期（{pd.month}月{pd.day}日），"
                f"请及时安排打款{phone_part}"
            )
            send_wechat("🏦 房东付款提醒", content)
            print(content + "\n")

    if not any_found:
        print("ℹ️  今日无需提醒")

    conn.close()


# ============================================================
#  交互式数据录入（方便测试 & 日常使用）
# ============================================================

def list_shops(conn):
    """列出所有门面，返回 {shop_id: shop_name}"""
    rows = conn.execute("SELECT shop_id, shop_name FROM Shops ORDER BY shop_id").fetchall()
    if not rows:
        return {}
    print("\n现有门面列表:")
    for r in rows:
        print(f"  [{r['shop_id']}] {r['shop_name']}")
    return {str(r["shop_id"]): r["shop_name"] for r in rows}


def add_data_interactive():
    """交互式录入数据"""
    conn = get_conn()

    shops = list_shops(conn)

    print("\n📝 请选择要添加的数据类型:")
    print("  1) 门面")
    print("  2) 收楼合同（房东）")
    print("  3) 出楼合同（租户）")
    print("  0) 退出")

    choice = input("请输入数字: ").strip()

    if choice == "1":
        name = input("门面名称: ").strip()
        if name:
            conn.execute("INSERT INTO Shops (shop_name) VALUES (?)", (name,))
            conn.commit()
            print(f"✅ 已添加门面: {name}")

    elif choice == "2":
        if not shops:
            print("⚠️  请先添加门面！")
            conn.close()
            add_data_interactive()
            return
        shop_id = input("门面ID: ").strip()
        if shop_id not in shops:
            print("❌ 门面ID 不存在")
        else:
            name = input("房东姓名: ").strip()
            date_str = input("下次付款日期 (YYYY-MM-DD): ").strip()
            try:
                datetime.strptime(date_str, DATE_FMT)
                conn.execute(
                    "INSERT INTO LandlordContracts (shop_id, landlord_name, next_payment_date) VALUES (?, ?, ?)",
                    (shop_id, name, date_str),
                )
                conn.commit()
                print(f"✅ 已添加房东合同: {name}")
            except ValueError:
                print("❌ 日期格式错误，应为 YYYY-MM-DD")

    elif choice == "3":
        if not shops:
            print("⚠️  请先添加门面！")
            conn.close()
            add_data_interactive()
            return
        shop_id = input("门面ID: ").strip()
        if shop_id not in shops:
            print("❌ 门面ID 不存在")
        else:
            name = input("租户姓名: ").strip()
            phone = input("租户手机号: ").strip()
            date_str = input("下次交租日期 (YYYY-MM-DD): ").strip()
            try:
                datetime.strptime(date_str, DATE_FMT)
                conn.execute(
                    "INSERT INTO TenantContracts (shop_id, tenant_name, tenant_phone, next_payment_date) VALUES (?, ?, ?, ?)",
                    (shop_id, name, phone, date_str),
                )
                conn.commit()
                print(f"✅ 已添加租户合同: {name}")
            except ValueError:
                print("❌ 日期格式错误，应为 YYYY-MM-DD")

    elif choice == "0":
        pass

    else:
        print("❌ 无效选择")
        conn.close()
        add_data_interactive()
        return

    conn.close()

    again = input("\n继续添加？(y/n): ").strip().lower()
    if again == "y":
        add_data_interactive()


# ============================================================
#  预览数据
# ============================================================

def show_data():
    """打印数据库中所有记录"""
    conn = get_conn()

    print("\n📋 门面列表:")
    for r in conn.execute("SELECT * FROM Shops").fetchall():
        print(f"  [{r['shop_id']}] {r['shop_name']}")

    print("\n📋 收楼合同（房东 -> 我方）:")
    for r in conn.execute("""
        SELECT l.contract_id, s.shop_name, l.landlord_name, l.next_payment_date
        FROM LandlordContracts l
        JOIN Shops s ON s.shop_id = l.shop_id
        ORDER BY l.next_payment_date
    """).fetchall():
        pay_date = r["next_payment_date"]
        print(f"  [{r['contract_id']}] {r['shop_name']} - 房东{r['landlord_name']} 下次付款: {pay_date}")

    print("\n📋 出楼合同（租户 -> 我方）:")
    for r in conn.execute("""
        SELECT t.contract_id, s.shop_name, t.tenant_name, t.tenant_phone, t.next_payment_date
        FROM TenantContracts t
        JOIN Shops s ON s.shop_id = t.shop_id
        ORDER BY t.next_payment_date
    """).fetchall():
        pay_date = r["next_payment_date"]
        print(f"  [{r['contract_id']}] {r['shop_name']} - 租户{r['tenant_name']}({r['tenant_phone']}) 下次交租: {pay_date}")

    conn.close()


# ============================================================
#  主入口
# ============================================================

def print_help():
    print(__doc__)


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print_help()
        return

    if "--init" in args:
        init_db()
        return

    if "--add" in args:
        init_db()
        add_data_interactive()
        return

    if "--show" in args:
        init_db()
        show_data()
        return

    init_db()
    run_daily_check()


if __name__ == "__main__":
    main()
