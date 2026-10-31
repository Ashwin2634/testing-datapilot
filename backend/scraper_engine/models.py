import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

class ProductData(BaseModel):
    # Silences Pydantic warning about field "model_number" starting with "model_"
    model_config = ConfigDict(protected_namespaces=())

    url: str
    merchant: str
    status: str = "success"       # "success" or "failed"
    engine_used: str = "http"     # "http" or "browser"
    title: Optional[str] = None
    brand: Optional[str] = None
    price: Optional[float] = None
    currency: str = "USD"
    in_stock: Optional[bool] = None
    sku: Optional[str] = None
    upc_gtin: Optional[str] = None
    model_number: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    description: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)
    specifications: Dict[str, Any] = Field(default_factory=dict)
    raw_json_ld: List[Dict[str, Any]] = Field(default_factory=list)
    raw_page_state: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None

    def to_excel_row(self) -> Dict[str, Any]:
        """Flattens nested lists/dicts into clean strings for Pandas/OpenPyXL Excel export."""
        return {
            "URL": self.url,
            "Merchant": self.merchant,
            "Status": self.status,
            "Engine Used": self.engine_used,
            "Title": self.title,
            "Brand": self.brand,
            "Price": self.price,
            "Currency": self.currency,
            "In Stock": self.in_stock,
            "SKU": self.sku,
            "UPC / GTIN": self.upc_gtin,
            "Model Number": self.model_number,
            "Rating": self.rating,
            "Review Count": self.review_count,
            "Description": self.description,
            "Primary Image": self.image_urls[0] if self.image_urls else None,
            "All Images": ", ".join(self.image_urls),
            "Specifications (JSON)": json.dumps(self.specifications, ensure_ascii=False),
            "Error": self.error_message,
        }