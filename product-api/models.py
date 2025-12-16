from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    price_cents: int = Field(..., ge=0)

class ProductUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    price_cents: int = Field(..., ge=0)

class Product(BaseModel):
    id: int
    name: str
    price_cents: int
    updated_at: datetime

class ProductResponse(BaseModel):
    source: str
    data: Product

class ConsistencyTestResult(BaseModel):
    updated_value: dict
    replica_value_immediately: Optional[Product]
    replica_value_after_200ms: Optional[Product]
    cached_value: Optional[Product]
    analysis: dict

class HealthStatus(BaseModel):
    redis: str
    primary: str
    replica: str
