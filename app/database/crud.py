import os
import csv
import json
import logging
from sqlalchemy.orm import Session
from .connection import Base, engine
from .models import EventModel, TransactionModel, AlertModel

logger = logging.getLogger("store_intelligence.crud")

def init_db():
    """Create all database tables."""
    # Ensure data directory exists
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized successfully.")

def clear_db(db: Session):
    """Clear all records from all tables."""
    db.query(EventModel).delete()
    db.query(TransactionModel).delete()
    db.query(AlertModel).delete()
    db.commit()
    logger.info("Database tables cleared.")

def create_event(db: Session, event_id: str, store_id: str, camera_id: str, 
                 customer_id: str, event_type: str, timestamp: str, extra_data: dict = None) -> EventModel:
    """Insert a new event. Ignores if event_id already exists (caching/idempotency)."""
    existing = db.query(EventModel).filter(EventModel.event_id == event_id).first()
    if existing:
        return existing
    
    db_event = EventModel(
        event_id=event_id,
        store_id=store_id,
        camera_id=camera_id,
        customer_id=customer_id,
        event_type=event_type,
        timestamp=timestamp,
        extra_data=json.dumps(extra_data) if extra_data else None
    )
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event

def create_transaction(db: Session, order_id: int, order_date: str, order_time: str,
                       store_id: str, product_id: str, brand_name: str, total_amount: float) -> TransactionModel:
    """Insert a transaction."""
    existing = db.query(TransactionModel).filter(TransactionModel.order_id == order_id).first()
    if existing:
        if existing.store_id != store_id:
            existing.store_id = store_id
            db.commit()
            db.refresh(existing)
        return existing
        
    db_trans = TransactionModel(
        order_id=order_id,
        order_date=order_date,
        order_time=order_time,
        store_id=store_id,
        product_id=product_id,
        brand_name=brand_name,
        total_amount=total_amount
    )
    db.add(db_trans)
    db.commit()
    db.refresh(db_trans)
    return db_trans


def create_alert(db: Session, alert_id: str, store_id: str, alert_type: str,
                 description: str, timestamp: str, severity: str) -> AlertModel:
    """Insert a new alert."""
    existing = db.query(AlertModel).filter(AlertModel.alert_id == alert_id).first()
    if existing:
        return existing
        
    db_alert = AlertModel(
        alert_id=alert_id,
        store_id=store_id,
        alert_type=alert_type,
        description=description,
        timestamp=timestamp,
        severity=severity
    )
    db.add(db_alert)
    db.commit()
    db.refresh(db_alert)
    return db_alert

def get_events(db: Session, store_id: str = None, camera_id: str = None, 
               event_type: str = None, limit: int = 1000):
    """Retrieve events with optional filters."""
    query = db.query(EventModel)
    if store_id:
        query = query.filter(EventModel.store_id == store_id)
    if camera_id:
        query = query.filter(EventModel.camera_id == camera_id)
    if event_type:
        query = query.filter(EventModel.event_type == event_type)
    return query.order_by(EventModel.timestamp.desc()).limit(limit).all()

def get_transactions(db: Session, store_id: str = None, limit: int = 5000):
    """Retrieve transactions with optional filters."""
    query = db.query(TransactionModel)
    if store_id:
        query = query.filter(TransactionModel.store_id == store_id)
    return query.all()

def get_alerts(db: Session, store_id: str = None, limit: int = 100):
    """Retrieve alerts with optional filters."""
    query = db.query(AlertModel)
    if store_id:
        query = query.filter(AlertModel.store_id == store_id)
    return query.order_by(AlertModel.timestamp.desc()).limit(limit).all()

def import_transactions_from_csv(db: Session, csv_path: str):
    """Load POS transactions CSV into database."""
    if not os.path.exists(csv_path):
        logger.warning(f"POS CSV file not found at {csv_path}. Skipping import.")
        return
    
    logger.info(f"Importing transactions from {csv_path}...")
    count = 0
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                order_id = int(row["order_id"])
                total_amount = float(row["total_amount"])
                # Handle possible whitespace in column names
                create_transaction(
                    db=db,
                    order_id=order_id,
                    order_date=row["order_date"].strip(),
                    order_time=row["order_time"].strip(),
                    store_id=row["store_id"].strip(),
                    product_id=row["product_id"].strip(),
                    brand_name=row["brand_name"].strip(),
                    total_amount=total_amount
                )
                count += 1
            except Exception as e:
                logger.error(f"Error parsing row {row}: {e}")
    logger.info(f"Imported {count} transactions.")
