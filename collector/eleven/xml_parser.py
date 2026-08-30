"""11번가 EUC-KR XML 응답 파서."""

from __future__ import annotations

from typing import Any, Iterable, Optional
from xml.etree import ElementTree


def decode_xml(content: bytes) -> str:
    """응답 선언이 불완전한 경우까지 고려해 UTF-8/EUC-KR/CP949 순서로 해석한다."""
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def element_to_data(element: ElementTree.Element) -> Any:
    children = list(element)
    if not children:
        return (element.text or "").strip()

    result: dict[str, Any] = {}
    for child in children:
        key = local_name(child.tag)
        value = element_to_data(child)
        if key in result:
            current = result[key]
            result[key] = current + [value] if isinstance(current, list) else [current, value]
        else:
            result[key] = value
    return result


def _direct_children(element: ElementTree.Element) -> dict[str, ElementTree.Element]:
    return {local_name(child.tag).lower(): child for child in list(element)}


def _text(
    children: dict[str, ElementTree.Element], *names: str
) -> Optional[str]:
    for name in names:
        node = children.get(name.lower())
        if node is not None:
            value = (node.text or "").strip()
            if value:
                return value
    return None


def _to_int(value: Optional[str], default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _to_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized in {"Y", "YES", "TRUE", "1"}:
        return True
    if normalized in {"N", "NO", "FALSE", "0"}:
        return False
    return None


def parse_categories(xml_text: str) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(xml_text)
    categories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for element in root.iter():
        children = _direct_children(element)
        disp_no = _text(children, "dispNo")
        disp_nm = _text(children, "dispNm")
        if not disp_no or not disp_nm or disp_no in seen:
            continue
        seen.add(disp_no)
        categories.append(
            {
                "disp_no": disp_no,
                "disp_nm": disp_nm,
                "parent_disp_no": _text(children, "parentDispNo"),
                "depth": _to_int(_text(children, "depth"), 0),
                "leaf_yn": bool(_to_bool(_text(children, "leafYn"))),
                "gbl_dlv_yn": _to_bool(_text(children, "gblDlvYn")),
                "eng_disp_yn": _to_bool(_text(children, "engDispYn")),
                "raw_data": element_to_data(element),
            }
        )
    return categories


def parse_products(xml_text: str) -> tuple[list[dict[str, Any]], int]:
    root = ElementTree.fromstring(xml_text)
    products: list[dict[str, Any]] = []
    total_count = 0
    for element in root.iter():
        name = local_name(element.tag).lower()
        if name == "totalcount":
            total_count = _to_int((element.text or "").strip(), 0) or 0
        if name != "product":
            continue
        children = _direct_children(element)
        if _text(children, "ProductCode"):
            data = element_to_data(element)
            products.append(data if isinstance(data, dict) else {})
    return products, total_count


def extract_api_error(xml_text: str) -> Optional[str]:
    """HTTP 200으로 전달되는 ErrorResponse의 메시지를 추출한다."""
    root = ElementTree.fromstring(xml_text)
    root_name = local_name(root.tag).lower()
    if "error" not in root_name:
        return None

    values: list[str] = []
    for element in root.iter():
        name = local_name(element.tag).lower()
        text = (element.text or "").strip()
        if text and name in {
            "errorcode",
            "errormessage",
            "message",
            "resultcode",
            "resultmessage",
        }:
            values.append(text)
    return " / ".join(values) or root_name


def get_value(item: dict[str, Any], *names: str) -> Any:
    lowered = {key.lower(): value for key, value in item.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def extract_category_path(item: dict[str, Any]) -> list[str]:
    path = [
        get_value(item, f"Category{i}", f"CategoryName{i}", f"category{i}")
        for i in range(1, 5)
    ]
    return [str(value).strip() for value in path if isinstance(value, str) and value.strip()]


def extract_category_disp_no(item: dict[str, Any]) -> Optional[str]:
    value = get_value(
        item,
        "CategoryDispNo",
        "DispNo",
        "CategoryCode",
        "CategoryPrd",
    )
    return str(value).strip() if value not in (None, "") else None


def iter_leaf_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_leaf_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_leaf_strings(child)
