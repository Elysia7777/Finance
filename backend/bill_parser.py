#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse locally exported WeChat, Alipay, and CSV bills into common transactions."""

import csv
import datetime as _datetime
import io
import json
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _cell_value(cell, shared):
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(cell.itertext())
    value = cell.find("main:v", _NS)
    if value is None:
        return ""
    text = value.text or ""
    if kind == "s":
        return shared[int(text)]
    if kind == "b":
        return text == "1"
    return text


def _xlsx_rows(contents):
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {item.attrib["Id"]: item.attrib["Target"]
                       for item in relationships.findall("pkg:Relationship", _NS)}
            sheet = workbook.find("main:sheets/main:sheet", _NS)
            if sheet is None:
                raise ValueError("Excel 文件中没有工作表")
            target = targets[sheet.attrib["{" + _NS["rel"] + "}id"]]
            sheet_path = target.lstrip("/")
            if not sheet_path.startswith("xl/"):
                sheet_path = posixpath.join("xl", sheet_path)
            sheet_path = posixpath.normpath(sheet_path)
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in strings.findall("main:si", _NS)]
            worksheet = ET.fromstring(archive.read(sheet_path))
            result = []
            for row in worksheet.findall(".//main:sheetData/main:row", _NS):
                cells = {}
                for cell in row.findall("main:c", _NS):
                    match = re.match(r"([A-Z]+)", cell.attrib["r"])
                    if not match:
                        continue
                    column = 0
                    for char in match.group(1):
                        column = column * 26 + ord(char) - ord("A") + 1
                    cells[column - 1] = _cell_value(cell, shared)
                if cells:
                    result.append([cells.get(i, "") for i in range(max(cells) + 1)])
            return result
    except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc):
            raise
        raise ValueError("无法读取该 Excel 文件，请确认它是有效的 .xlsx 账单") from exc


def _read_rows(contents, filename):
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if extension == "xlsx":
        return _xlsx_rows(contents)
    if extension != "csv":
        raise ValueError("暂支持微信/支付宝导出的 .xlsx 和 .csv 文件（暂不支持 .xls）")
    text = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = contents.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV 编码无法识别，请另存为 UTF-8 或 GB18030")
    try:
        return list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ValueError("CSV 格式无效") from exc


def _normal(value):
    return re.sub(r"[\s:：,，()（）\-/]+", "", str(value or "")).lower()


def _find_header(rows):
    aliases = {
        "date": ("交易时间", "创建时间", "付款时间", "交易日期", "日期", "时间"),
        "amount": ("金额元", "金额", "收支金额", "交易金额", "实收金额"),
        "flow": ("收支", "收/支", "资金流向", "收支类型"),
    }
    for row_index, row in enumerate(rows):
        values = [_normal(value) for value in row]
        mapping = {}
        for field, names in aliases.items():
            for index, value in enumerate(values):
                if any(_normal(name) in value for name in names):
                    mapping[field] = index
                    break
        if "date" in mapping and "amount" in mapping:
            optional = {
                "flow": ("收/支", "收支", "收支类型", "资金流向"),
                "category": ("交易类型", "类型", "业务类型"),
                "merchant": ("交易对方", "对方名称", "商户名称", "商家名称"),
                "product": ("商品", "商品名称", "商品说明", "摘要"),
                "status": ("当前状态", "交易状态", "状态"),
                "method": ("支付方式", "收/付款方式"),
                "reference": ("交易单号", "交易号", "订单号"),
                "note": ("备注", "附言"),
            }
            for field, names in optional.items():
                for index, value in enumerate(values):
                    if any(_normal(name) in value for name in names):
                        mapping[field] = index
                        break
            return row_index, mapping
    raise ValueError("未找到账单表头。请确认文件包含“交易时间/日期”和“金额”列")


def _get(row, mapping, key):
    index = mapping.get(key)
    return str(row[index]).strip() if index is not None and index < len(row) else ""


def _parse_date(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("缺少交易日期")
    try:
        serial = float(text)
        if 20000 <= serial <= 100000:
            stamp = _datetime.datetime(1899, 12, 30) + _datetime.timedelta(days=serial)
            return stamp.strftime("%Y-%m-%d")
    except ValueError:
        pass
    text = text.replace("年", "-").replace("月", "-").replace("日", " ")
    text = text.replace("/", "-").replace(".", "-").strip()
    match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
    if match:
        try:
            return _datetime.date.fromisoformat(match.group(1)).isoformat()
        except ValueError as exc:
            raise ValueError("账单中包含无效日期：" + text) from exc
    raise ValueError("无法识别交易日期：" + text)


def _amount(value):
    text = re.sub(r"[,\s￥¥元]", "", str(value or ""))
    text = text.replace("−", "-").replace("－", "-")
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError("账单中包含无效金额：" + str(value)) from exc
    if number != number or abs(number) == float("inf"):
        raise ValueError("账单中包含无效金额：" + str(value))
    return abs(number)


def parse_bill(file_bytes, filename):
    """Return normalized transaction records from a supported export."""
    rows = _read_rows(file_bytes, filename)
    header_index, mapping = _find_header(rows)
    source = "微信" if "微信" in filename else "支付宝" if "支付宝" in filename else "导入账单"
    records = []
    for line_number, row in enumerate(rows[header_index + 1:], header_index + 2):
        if not row or not any(str(value).strip() for value in row):
            continue
        status = _get(row, mapping, "status")
        if any(word in status for word in ("关闭", "失败", "待付款", "交易关闭")):
            continue
        raw_amount = _get(row, mapping, "amount")
        if not raw_amount:
            continue
        try:
            date = _parse_date(_get(row, mapping, "date"))
            amount = _amount(raw_amount)
        except ValueError as exc:
            raise ValueError("第 %d 行：%s" % (line_number, exc)) from exc
        if amount == 0:
            continue
        flow = _get(row, mapping, "flow")
        if not flow:
            flow = "收入" if any(word in status for word in ("退款", "收款")) else "支出"
        direction = "income" if any(word in flow for word in ("收入", "收款", "退款")) else "expense"
        category_name = _get(row, mapping, "category")
        merchant = _get(row, mapping, "merchant")
        product = _get(row, mapping, "product")
        note = " / ".join(part for part in (product, _get(row, mapping, "note")) if part and part != "/")
        raw = {"row": [str(value) for value in row], "line": line_number}
        records.append({
            "date": date,
            "amount": amount,
            "direction": direction,
            "category": category_name or "未分类",
            "bucket": "daily",
            "merchant": merchant,
            "note": note,
            "source": source,
            "sourceRef": _get(row, mapping, "reference"),
            "raw": json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        })
    if not records:
        raise ValueError("文件中没有可导入的有效交易记录")
    return records
