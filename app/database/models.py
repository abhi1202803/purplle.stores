from sqlalchemy import Column, Integer, String, Float, Text
from .connection import Base

class EventModel(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    event_id = Column(String, unique=True, index=True)
    store_id = Column(String, index=True)
    camera_id = Column(String, index=True)
    customer_id = Column(String, index=True)
    event_type = Column(String, index=True)
    timestamp = Column(String, index=True)
    extra_data = Column(Text, nullable=True)  # Stored as JSON string

class TransactionModel(Base):
    __tablename__ = "transactions"

    order_id = Column(Integer, primary_key=True, index=True)
    order_date = Column(String, index=True)
    order_time = Column(String, index=True)
    store_id = Column(String, index=True)
    product_id = Column(String, index=True)
    brand_name = Column(String, index=True)
    total_amount = Column(Float)

class AlertModel(Base):
    __tablename__ = "alerts"

    alert_id = Column(String, primary_key=True, index=True)
    store_id = Column(String, index=True)
    alert_type = Column(String, index=True)
    description = Column(Text)
    timestamp = Column(String, index=True)
    severity = Column(String)  # Info, Warning, Critical
