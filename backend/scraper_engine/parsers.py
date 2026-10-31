import json
import re
from typing import Any, Dict, List, Optional
import extruct
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from .models import ProductData
from .config import ScraperConfig

class ParsingLayer:
    @classmethod
    def parse(cls, html: str, url: str, engine_used: str) -> ProductData:
        merchant = ScraperConfig.extract_base_domain(url)
        tree = HTMLParser(html)

        # 1. EXTRACT JSON-LD & OPENGRAPH
        structured = extruct.extract(
            html, base_url=url, syntaxes=["json-ld", "opengraph"], uniform=True
        )
        json_ld_list = structured.get("json-ld", [])
        og_list = structured.get("opengraph", [])

        # 2. EXTRACT PAGE STATES (__NEXT_DATA__, Apollo, Preloaded State)
        page_state = cls._extract_page_states(tree, html)

        product = ProductData(
            url=url,
            merchant=merchant,
            engine_used=engine_used,
            raw_json_ld=json_ld_list,
            raw_page_state=page_state,
        )

        # 3. POPULATE FROM JSON-LD PRODUCT SCHEMA
        cls._populate_from_json_ld(product, json_ld_list)

        # 4. POPULATE FROM PAGE STATE (Walmart __NEXT_DATA__, etc.)
        cls._populate_from_page_state(product, page_state, merchant)

        # 5. MERCHANT-SPECIFIC DOM FALLBACKS (Amazon, Menards, & missing fields)
        cls._populate_from_dom(product, tree, merchant, og_list)

        return product

    @staticmethod
    def _extract_page_states(tree: HTMLParser, html: str) -> Dict[str, Any]:
        states: Dict[str, Any] = {}

        # A. Next.js State (Walmart, TractorSupply, etc.)
        next_node = tree.css_first("script#__NEXT_DATA__")
        if next_node and next_node.text():
            try:
                states["__NEXT_DATA__"] = json.loads(next_node.text())
            except Exception:
                pass

        # B. Window Preloaded / Redux / Apollo States (HomeDepot, Lowes, BestBuy)
        patterns = {
            "apollo_state": r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});\s*</script>",
            "preloaded_state": r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});\s*</script>",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    states[key] = json.loads(match.group(1))
                except Exception:
                    pass

        return states

    @classmethod
    def _populate_from_json_ld(cls, product: ProductData, json_ld_list: List[Dict[str, Any]]):
        product_nodes = []
        for item in json_ld_list:
            if isinstance(item, dict):
                if item.get("@type") in ("Product", "ProductGroup", ["Product"]):
                    product_nodes.append(item)
                elif "@graph" in item and isinstance(item["@graph"], list):
                    for sub in item["@graph"]:
                        if isinstance(sub, dict) and sub.get("@type") in ("Product", "ProductGroup"):
                            product_nodes.append(sub)

        for node in product_nodes:
            product.title = product.title or node.get("name")
            product.description = product.description or node.get("description")
            product.sku = product.sku or str(node.get("sku") or node.get("productID") or "") or None
            product.upc_gtin = product.upc_gtin or str(
                node.get("gtin13") or node.get("gtin12") or node.get("gtin") or ""
            ) or None
            product.model_number = product.model_number or str(node.get("model") or node.get("mpn") or "") or None

            # Brand
            brand = node.get("brand")
            if isinstance(brand, dict):
                product.brand = product.brand or brand.get("name")
            elif isinstance(brand, str):
                product.brand = product.brand or brand

            # Images
            imgs = node.get("image")
            if isinstance(imgs, str):
                product.image_urls.append(imgs)
            elif isinstance(imgs, list):
                for i in imgs:
                    if isinstance(i, str):
                        product.image_urls.append(i)
                    elif isinstance(i, dict) and i.get("url"):
                        product.image_urls.append(i["url"])

            # Rating
            rating_obj = node.get("aggregateRating")
            if isinstance(rating_obj, dict):
                product.rating = cls._to_float(rating_obj.get("ratingValue"))
                product.review_count = cls._to_int(rating_obj.get("reviewCount") or rating_obj.get("ratingCount"))

            # Offers / Price / Stock
            offers = node.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price_val = offers.get("price") or offers.get("lowPrice")
                if price_val is not None and product.price is None:
                    product.price = cls._to_float(price_val)
                product.currency = offers.get("priceCurrency") or product.currency
                avail = str(offers.get("availability", "")).lower()
                if avail:
                    product.in_stock = "instock" in avail or "in_stock" in avail or "onlineonly" in avail

            # Additional Properties / Specs
            add_props = node.get("additionalProperty")
            if isinstance(add_props, list):
                for prop in add_props:
                    if isinstance(prop, dict) and prop.get("name") and prop.get("value"):
                        product.specifications[str(prop["name"])] = prop["value"]

    @classmethod
    def _populate_from_page_state(cls, product: ProductData, state: Dict[str, Any], merchant: str):
        if "walmart.com" in merchant and "__NEXT_DATA__" in state:
            try:
                apollo = (
                    state["__NEXT_DATA__"]
                    .get("props", {})
                    .get("pageProps", {})
                    .get("initialData", {})
                    .get("data", {})
                    .get("product", {})
                )
                if apollo:
                    product.title = product.title or apollo.get("name")
                    product.brand = product.brand or apollo.get("brand")
                    product.sku = product.sku or apollo.get("usItemId")
                    product.upc_gtin = product.upc_gtin or apollo.get("upc")
                    product.model_number = product.model_number or apollo.get("model")
                    product.description = product.description or apollo.get("shortDescription")
                    price_info = apollo.get("priceInfo", {}).get("currentPrice", {})
                    if price_info and product.price is None:
                        product.price = cls._to_float(price_info.get("price"))
                    avail = apollo.get("availabilityStatusV2", {}).get("value", "")
                    if avail and product.in_stock is None:
                        product.in_stock = avail.upper() == "IN_STOCK"
            except Exception:
                pass

    @classmethod
    def _populate_from_dom(
        cls, product: ProductData, tree: HTMLParser, merchant: str, og_list: List[Dict[str, Any]]
    ):
        # 1. Amazon DOM Parser
        if "amazon.com" in merchant:
            product.title = product.title or cls._text(tree, "#productTitle")
            product.brand = product.brand or cls._text(tree, "#bylineInfo")
            price_str = (
                cls._text(tree, "#corePrice_feature_div .a-offscreen")
                or cls._text(tree, "#corePriceDisplay_desktop_feature_div .a-offscreen")
                or cls._text(tree, "#priceblock_ourprice")
                or cls._text(tree, ".a-price .a-offscreen")
            )
            if price_str and product.price is None:
                product.price = cls._to_float(price_str)

            avail_str = cls._text(tree, "#availability")
            if avail_str and product.in_stock is None:
                product.in_stock = "in stock" in avail_str.lower()

            rating_str = cls._text(tree, "#acrPopover")
            if rating_str and product.rating is None:
                product.rating = cls._to_float(rating_str.split("out of")[0])

            for row in tree.css("#productDetails_techSpec_section_1 tr, #detailBullets_feature_div li"):
                text = row.text(separator=" ", strip=True)
                if ":" in text:
                    k, v = text.split(":", 1)
                    k_clean, v_clean = k.strip(), v.strip()
                    product.specifications[k_clean] = v_clean
                    if "asin" in k_clean.lower() and not product.sku:
                        product.sku = v_clean

        # 2. Menards DOM Fallback
        elif "menards.com" in merchant:
            product.title = product.title or cls._text(tree, "h1.itemTitle, h1")
            price_str = cls._text(tree, "#itemPrice, .price-val, [data-testid='price']")
            if price_str and product.price is None:
                product.price = cls._to_float(price_str)

        # 3. Universal OpenGraph & HTML Tag Fallbacks
        if og_list and isinstance(og_list[0], dict):
            og = og_list[0]
            product.title = product.title or og.get("og:title")
            product.description = product.description or og.get("og:description")
            if og.get("og:image") and not product.image_urls:
                product.image_urls.append(og["og:image"])
            if product.price is None and og.get("product:price:amount"):
                product.price = cls._to_float(og.get("product:price:amount"))

        if not product.title:
            product.title = cls._text(tree, "h1") or cls._text(tree, "title")

        product.image_urls = list(dict.fromkeys([img for img in product.image_urls if img]))

    @staticmethod
    def _text(tree: HTMLParser, selector: str) -> Optional[str]:
        node = tree.css_first(selector)
        return node.text(strip=True) if node else None

    @staticmethod
    def _to_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        cleaned = re.sub(r"[^\d.]", "", str(val))
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    @staticmethod
    def _to_int(val: Any) -> Optional[int]:
        if val is None:
            return None
        cleaned = re.sub(r"[^\d]", "", str(val))
        try:
            return int(cleaned) if cleaned else None
        except ValueError:
            return None