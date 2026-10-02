"""Generate fully synthetic bill fixtures for tests.

Everything here is invented: merchants like 测试咖啡, owners like 张测试,
card tails 0000/1111, order ids TEST-*.  No real person, card, account or
transaction is used anywhere.  Run from the repository root:

    python scripts/make_fixtures.py

Regenerates the files under tests/fixtures/synthetic/ deterministically.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "synthetic"
ENC = OUT / "encrypted"

# Fixed synthetic passwords for synthetic fixtures only. They protect fake
# data and exist so tests can exercise password-protected archives.
ZIP_PASSWORDS = {"alipay": "test-zip-pass-01", "wechat": "test-zip-pass-02"}
PDF_PASSWORDS = {"icbc": "test-pdf-pass-03", "boc": "test-pdf-pass-04"}

EXCEL_EPOCH = datetime(1899, 12, 30)
OWNER = "张测试"
ICBC_CARD = "工商银行储蓄卡(0000)"
BOC_CARD = "中国银行储蓄卡(1111)"


def excel_serial(dt: datetime) -> str:
    seconds = (dt - EXCEL_EPOCH).total_seconds()
    return f"{seconds / 86400:.12f}"


def money(value: str) -> str:
    return f"{Decimal(value):.2f}"


# ---------------------------------------------------------------------------
# Scenario data (all synthetic)
# ---------------------------------------------------------------------------

ALIPAY_ROWS = [
    # 交易分类, 交易对方, 对方账号, 商品说明, 收/支, 金额, 收/付款方式, 交易状态, 交易订单号, 商家订单号, 备注
    ("交通出行", "测试打车平台", "account@test.example", "快车", "支出", "88.00", "余额", "交易成功", "TESTZFB0001", "TMERCHANT01", ""),
    ("交通出行", "测试打车平台", "account@test.example", "快车退款", "收入", "30.00", "余额", "退款成功", "TESTZFB0001_REF1", "", ""),
    ("餐饮美食", "测试奶茶店", "shop@test.example", "珍珠奶茶", "支出", "35.00", ICBC_CARD, "交易成功", "TESTZFB0002", "TMERCHANT02", ""),
    ("日用百货", "测试超市", "market@test.example", "生活用品", "支出", "128.00", "余额", "交易成功", "TESTZFB0003", "TMERCHANT03", ""),
    ("文化娱乐", "测试文具店", "wenju@test.example", "笔记本", "支出", "25.00", "余额", "交易成功", "TESTZFB0004", "TMERCHANT04", ""),
    ("文化娱乐", "测试文具店", "wenju@test.example", "笔记本退款", "收入", "25.00", "余额", "退款成功", "TESTZFB0004_REF1", "", ""),
    ("投资理财", "余额宝", "-（自动转入）", "余额宝-转入", "不计收支", "1000.00", "余额", "交易成功", "TESTZFB0005", "", ""),
    ("工资", "测试科技有限公司", "hr@test.example", "6月工资", "收入", "5000.00", "余额", "交易成功", "TESTZFB0006", "", ""),
    ("其他", "神秘未知商户XYZ", "", " mystery item", "支出", "999.00", "余额", "交易成功", "TESTZFB0007", "", ""),
    ("餐饮美食", "测试外卖平台", "waimai@test.example", "午餐", "支出", "45.50", "余额", "交易成功", "TESTZFB0008", "TMERCHANT08", ""),
    ("充值", "测试视频网站", "vip@test.example", "季度会员", "支出", "68.00", "余额", "交易成功", "TESTZFB0009", "TMERCHANT09", ""),
]

# ICBC statement rows (all synthetic).
# Columns follow the parser's contract:
#   0 交易日期(含时间) 1 记账日期 2 摘要 3 币种 4 钞汇 5 网点号
#   6 业务类型 7 交易场所 8 交易金额 9 账户余额 10 对方户名 11 对方账号 12 用途
ICBC_OPENING = Decimal("10000.00")
ICBC_ROWS = [
    ("2026-06-01 12:31:00", "2026-06-01", "跨行消费", "人民币", "钞", "0001", "消费", "杭州", "-35.00", "支付宝-张测试", "0000000000", "消费"),
    ("2026-06-02 09:00:00", "2026-06-02", "消费", "人民币", "钞", "0001", "消费", "苏州", "-20.00", "测试A商店", "-", "消费"),
    ("2026-06-02 09:01:00", "2026-06-02", "消费", "人民币", "钞", "0001", "消费", "苏州", "-20.00", "测试B商店", "-", "消费"),
    ("2026-06-04 12:10:00", "2026-06-04", "消费", "人民币", "钞", "0001", "消费", "杭州", "-45.50", "测试外卖平台", "-", "消费"),
    ("2026-06-08 10:00:00", "2026-06-08", "支付宝转账", "人民币", "钞", "0001", "转账", "", "-1000.00", f"支付宝-{OWNER}", "-", "转账"),
    ("2026-06-08 14:31:00", "2026-06-08", "财付通提现", "人民币", "钞", "0001", "提现", "", "499.50", f"财付通-{OWNER}", "-", "提现"),
    ("2026-06-10 12:00:00", "2026-06-10", "消费退款", "人民币", "钞", "0001", "退款", "", "50.00", "测试超市", "-", "退款"),
]

# BOC statement rows in ascending order (the PDF lists newest first).
#   0 记账日期 1 记账时间 2 交易摘要 3 金额 4 账户余额 5 交易类型明细
#   6 支付方式 7 营业网点 8 附言 9 对方户名 10 对方账号 11 对方开户行
BOC_OPENING = Decimal("5000.00")
BOC_ROWS_ASC = [
    ("2026-06-06", "20:01:00", "消费", "-60.00", "微信支付", "电子银行", "测试支行营业部", "电影票", "测试电影", "1111111111", "测试银行"),
    ("2026-06-09", "09:00:00", "数币兑出", "-300.00", "数字人民币兑出", "电子银行", "测试支行营业部", "数币兑出", "-", "-", "测试银行"),
]

WECHAT_ROWS = [
    # 交易类型, 交易对方, 商品, 收/支, 金额(元), 支付方式, 当前状态, 交易单号, 商户单号, 备注
    ("商户消费", "测试外卖平台", "午餐", "支出", "45.50", "零钱", "支付成功", "TESTWX0001", "WMERCHANT01", "/"),
    ("商户消费", "测试电影", "电影票", "支出", "60.00", BOC_CARD, "支付成功", "TESTWX0002", "WMERCHANT02", "/"),
    ("转账", f"{OWNER}", "转账备注: 分摊测试", "支出", "20.00", "零钱", "已收款", "TESTWX0003", "", "已转账"),
    ("商户消费", "测试B商店", "小商品", "支出", "20.00", "零钱", "支付成功", "TESTWX0004", "WMERCHANT04", "/"),
    ("零钱提现", "零钱提现", "零钱提现-到银行卡", "/", "500.00", ICBC_CARD, "提现已到账", "TESTWX0005", "", "服务费¥0.50"),
    ("微信红包", "测试朋友", "红包", "收入", "66.66", "", "已存入零钱", "TESTWX0006", "", "/"),
    ("退款", "测试外卖平台", "退款-午餐", "收入", "45.50", "零钱", "已全额退款", "TESTWX0007", "WMERCHANT01", "/"),
    ("商户消费", "测试咖啡", "拿铁", "支出", "18.00", "零钱", "支付成功", "TESTWX0008", "WMERCHANT08", "/"),
]

ALIPAY_DATETIMES = [
    "2026-06-03 09:00:00",  # 1 打车 (refund original)
    "2026-06-05 14:00:00",  # 1_REF1 退款
    "2026-06-01 12:30:30",  # 2 奶茶
    "2026-06-02 18:00:00",  # 3 超市
    "2026-06-05 10:00:00",  # 4 文具 (full refund original)
    "2026-06-06 10:00:00",  # 4_REF1 退款
    "2026-06-07 08:00:00",  # 5 余额宝
    "2026-06-20 09:00:00",  # 6 工资
    "2026-06-21 20:00:00",  # 7 未知商户
    "2026-06-04 12:05:00",  # 8 外卖
    "2026-06-22 19:30:00",  # 9 视频
]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def gen_alipay_csv() -> bytes:
    buffer = io.StringIO(newline="")
    counts = {"收入": [0, Decimal("0")], "支出": [0, Decimal("0")], "不计收支": [0, Decimal("0")]}
    for row in ALIPAY_ROWS:
        counts[row[4]][0] += 1
        counts[row[4]][1] += Decimal(row[5])
    preamble = [
        "------------------------",
        "导出信息：[测试]",
        f"姓名：{OWNER}",
        "支付宝账户：test-alipay@example.com",
        "起始时间：[2026-06-01 00:00:00] 终止时间：[2026-06-30 24:00:00]",
        "导出交易类型：[全部]",
        "------------------",
        f"共{len(ALIPAY_ROWS)}笔记录",
        f"收入：{counts['收入'][0]}笔 {money(str(counts['收入'][1]))}元",
        f"支出：{counts['支出'][0]}笔 {money(str(counts['支出'][1]))}元",
        f"不计收支：{counts['不计收支'][0]}笔 {money(str(counts['不计收支'][1]))}元",
        "------------------",
        "分隔线",
    ]
    writer = csv.writer(buffer)
    for line in preamble:
        buffer.write(line + "\n")
    header = [
        "交易时间", "交易分类", "交易对方", "对方账号", "商品说明", "收/支",
        "金额", "收/付款方式", "交易状态", "交易订单号", "商家订单号", "备注",
    ]
    writer.writerow(header)
    for dt, row in zip(ALIPAY_DATETIMES, ALIPAY_ROWS):
        writer.writerow([dt, *row])
    return buffer.getvalue().encode("gb18030")


def gen_wechat_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    counts = {"收入": [0, Decimal("0")], "支出": [0, Decimal("0")], "中性交易": [0, Decimal("0")]}
    for row in WECHAT_ROWS:
        flow = {"收入": "收入", "支出": "支出"}.get(row[3], "中性交易")
        counts[flow][0] += 1
        counts[flow][1] += Decimal(row[4])

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "微信支付账单明细"
    ws["A4"] = "起始时间: 2026-06-01 00:00:00    终止时间: 2026-06-30 23:59:59"
    ws["A7"] = f"收入：{counts['收入'][0]}笔 {money(str(counts['收入'][1]))}元"
    ws["A8"] = f"支出：{counts['支出'][0]}笔 {money(str(counts['支出'][1]))}元"
    ws["A9"] = f"中性交易：{counts['中性交易'][0]}笔 {money(str(counts['中性交易'][1]))}元"
    ws["A10"] = f"共{len(WECHAT_ROWS)}笔记录"
    ws["A11"] = "导出类型：全部"
    ws["A12"] = "导出时间：2026-07-01 00:00:00"
    ws["A13"] = "------------------微信支付账单明细--------------------"
    headers = [
        "交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
        "支付方式", "当前状态", "交易单号", "商户单号", "备注",
    ]
    for column, header in enumerate(headers, start=1):
        ws.cell(row=18, column=column, value=header)
    datetimes = [
        "2026-06-04 12:10:00",  # 外卖
        "2026-06-06 20:00:00",  # 电影
        "2026-06-12 09:00:00",  # 转账分摊
        "2026-06-12 09:05:00",  # B商店
        "2026-06-08 14:30:00",  # 提现
        "2026-06-15 10:00:00",  # 红包
        "2026-06-16 10:00:00",  # 退款
        "2026-06-18 08:30:00",  # 咖啡
    ]
    for offset, (dt, row) in enumerate(zip(datetimes, WECHAT_ROWS)):
        excel_row = 19 + offset
        ws.cell(row=excel_row, column=1, value=float(excel_serial(datetime.strptime(dt, "%Y-%m-%d %H:%M:%S"))))
        for column, value in enumerate(row, start=2):
            ws.cell(row=excel_row, column=column, value=value)
    wb.save(path)


def _balance_chain(opening: Decimal, amounts) -> list[str]:
    balances = []
    current = opening
    for amount in amounts:
        current += Decimal(amount)
        balances.append(money(str(current)))
    return balances


def _register_chinese_font() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:  # noqa: BLE001 - already registered
        pass


def gen_icbc_pdf(path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    _register_chinese_font()
    balances = _balance_chain(ICBC_OPENING, [r[8] for r in ICBC_ROWS])
    header = [
        "交易日期", "记账日期", "摘要", "币种", "钞汇", "网点号",
        "业务类型", "交易场所", "交易金额", "账户余额", "对方户名", "对方账号", "用途",
    ]
    rows = [header]
    for row, balance in zip(ICBC_ROWS, balances):
        date, book_date, summary, currency, note_type, branch, raw_type, location, amount, counterparty, account, usage = row
        rows.append([
            date, book_date, summary, currency, note_type, branch,
            raw_type, location or "-", money(amount), balance, counterparty, account, usage,
        ])

    doc = SimpleDocTemplate(str(path), pagesize=A4, title="测试银行流水")
    style = getSampleStyleSheet()
    cn_style = ParagraphStyle("CN", parent=style["Heading2"], fontName="STSong-Light", fontSize=12)
    cell_style = ParagraphStyle("CNCell", parent=style["Normal"], fontName="STSong-Light", fontSize=7)
    # Wrap every cell in a Paragraph so Chinese text uses the CID font.
    body = [[Paragraph(str(cell), cell_style) for cell in row] for row in rows]
    story = [Paragraph("测试银行 储蓄卡流水（合成数据）", cn_style), _table(body, 13)]
    doc.build(story)


def gen_boc_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    _register_chinese_font()
    # Newest first in the PDF; balance column shows the balance after each row.
    balances = _balance_chain(BOC_OPENING, [r[3] for r in BOC_ROWS_ASC])
    rows_desc = list(reversed(list(zip(BOC_ROWS_ASC, balances))))

    header = [
        "记账日期", "记账时间", "交易摘要", "金额", "账户余额", "交易类型明细",
        "支付方式", "营业网点", "附言", "对方户名", "对方账号", "对方开户行",
    ]
    rows = [header]
    for row, balance in rows_desc:
        date, time_, summary, amount, raw_type, method, branch, note, counterparty, account, counterparty_bank = row
        rows.append([
            date, time_, summary, money(amount), balance, raw_type,
            method, branch, note, counterparty, account, counterparty_bank,
        ])

    doc = SimpleDocTemplate(str(path), pagesize=A4, title="测试中行流水")
    style = getSampleStyleSheet()
    cn_style = ParagraphStyle("CN", parent=style["Heading2"], fontName="STSong-Light", fontSize=12)
    cell_style = ParagraphStyle("CNCell", parent=style["Normal"], fontName="STSong-Light", fontSize=7)
    body = [[Paragraph(str(cell), cell_style) for cell in row] for row in rows]
    story = [Paragraph("测试中行 储蓄卡流水（合成数据）", cn_style), _table(body, 12)]
    doc.build(story)


def _table(rows, columns: int) -> Table:
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=[70] * columns)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTSIZE", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def main() -> None:
    ENC.mkdir(parents=True, exist_ok=True)

    # Plain fixtures
    (OUT / "alipay.csv").write_bytes(gen_alipay_csv())
    gen_wechat_xlsx(OUT / "wechat.xlsx")
    gen_icbc_pdf(OUT / "icbc.pdf")
    gen_boc_pdf(OUT / "boc.pdf")

    # Encrypted fixtures: real Alipay/WeChat zips use legacy ZipCrypto, which
    # is what the parser (stdlib zipfile) supports.  A minimal ZipCrypto
    # writer lives in fixtures_zipcrypto.py; passwords are synthetic and
    # committed on purpose (they protect only fake data).
    from fixtures_zipcrypto import write_zipcrypto_zip

    payloads = {
        "alipay": (
            OUT / "alipay.csv",
            "支付宝交易流水_测试.csv",
            ZIP_PASSWORDS["alipay"],
        ),
        "wechat": (
            OUT / "wechat.xlsx",
            "微信支付账单_测试.xlsx",
            ZIP_PASSWORDS["wechat"],
        ),
    }
    for name, (source, inner_name, password) in payloads.items():
        write_zipcrypto_zip(
            ENC / f"{name}_encrypted.zip",
            [(source.read_bytes(), inner_name)],
            password,
        )

    import pikepdf

    for name in ("icbc", "boc"):
        with pikepdf.open(OUT / f"{name}.pdf") as pdf:
            pdf.save(ENC / f"{name}_encrypted.pdf", encryption=pikepdf.Encryption(user=PDF_PASSWORDS[name], owner=PDF_PASSWORDS[name]))

    print("fixtures written to", OUT)


if __name__ == "__main__":
    main()
