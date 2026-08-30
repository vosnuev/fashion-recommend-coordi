import unittest

from xml_parser import (
    decode_xml,
    extract_api_error,
    extract_category_disp_no,
    get_value,
    parse_categories,
    parse_products,
)


CATEGORY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CategoryResponse>
  <categories>
    <category>
      <depth>2</depth>
      <dispNm>티셔츠</dispNm>
      <dispNo>1001234</dispNo>
      <parentDispNo>1001000</parentDispNo>
      <gblDlvYn>Y</gblDlvYn>
      <engDispYn>N</engDispYn>
      <leafYn>Y</leafYn>
    </category>
  </categories>
</CategoryResponse>
"""

PRODUCT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ProductSearchResponse>
  <Products>
    <TotalCount>1</TotalCount>
    <Product>
      <ProductCode>123456789</ProductCode>
      <ProductName><![CDATA[화이트 반팔 티셔츠]]></ProductName>
      <ProductPrice>20000</ProductPrice>
      <SalePrice>15000</SalePrice>
      <ProductImage>https://example.com/product.jpg</ProductImage>
      <DetailPageUrl>https://example.com/product</DetailPageUrl>
      <Seller>테스트몰</Seller>
      <Rating>4.5</Rating>
      <ReviewCount>12</ReviewCount>
      <BuySatisfy>90</BuySatisfy>
      <Delivery>무료배송</Delivery>
      <DispNo>1001234</DispNo>
      <Benefit><Discount>5000</Discount></Benefit>
    </Product>
  </Products>
</ProductSearchResponse>
"""

ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ErrorResponse>
  <ErrorCode>401</ErrorCode>
  <ErrorMessage>invalid key</ErrorMessage>
</ErrorResponse>
"""


class XmlParserTests(unittest.TestCase):
    def test_decode_euc_kr(self):
        text = "반팔 티셔츠"
        self.assertEqual(decode_xml(text.encode("euc-kr")), text)

    def test_parse_categories(self):
        categories = parse_categories(CATEGORY_XML)
        self.assertEqual(len(categories), 1)
        self.assertEqual(categories[0]["disp_no"], "1001234")
        self.assertEqual(categories[0]["parent_disp_no"], "1001000")
        self.assertTrue(categories[0]["leaf_yn"])
        self.assertTrue(categories[0]["gbl_dlv_yn"])
        self.assertFalse(categories[0]["eng_disp_yn"])

    def test_parse_products(self):
        products, total_count = parse_products(PRODUCT_XML)
        self.assertEqual(total_count, 1)
        self.assertEqual(len(products), 1)
        self.assertEqual(get_value(products[0], "ProductCode"), "123456789")
        self.assertEqual(extract_category_disp_no(products[0]), "1001234")
        self.assertEqual(
            get_value(products[0], "ProductDetailUrl", "DetailPageUrl"),
            "https://example.com/product",
        )
        self.assertEqual(products[0]["Benefit"]["Discount"], "5000")

    def test_extract_api_error(self):
        self.assertEqual(extract_api_error(ERROR_XML), "401 / invalid key")
        self.assertIsNone(extract_api_error(PRODUCT_XML))


if __name__ == "__main__":
    unittest.main()
