import os
import json
import csv
import logging
import random
import uuid
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.database.models import EventModel, TransactionModel, AlertModel
from app.database.crud import create_event, create_transaction, create_alert

logger = logging.getLogger("store_intelligence.simulator")

def parse_datetime(date_str: str, time_str: str) -> datetime:
    """Parse date (DD-MM-YYYY) and time (HH:MM:SS) to datetime."""
    # Handle order_date format like '10-04-2026' or '2026-04-10'
    try:
        if "-" in date_str:
            parts = date_str.split("-")
            if len(parts[0]) == 4: # YYYY-MM-DD
                dt_str = f"{date_str} {time_str}"
                return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            else: # DD-MM-YYYY
                dt_str = f"{date_str} {time_str}"
                return datetime.strptime(dt_str, "%d-%m-%Y %H:%M:%S")
    except Exception as e:
        logger.error(f"Error parsing date/time ({date_str}, {time_str}): {e}")
    # Default fallback
    return datetime(2026, 4, 10, 12, 0, 0)

def populate_sample_events(db: Session, sample_events_path: str):
    """Load sample_events.jsonl into the database."""
    if not os.path.exists(sample_events_path):
        # Fallback path search
        sample_events_path = os.path.join("d:/Desktop/purple_round2", sample_events_path)
        
    if not os.path.exists(sample_events_path):
        logger.warning(f"sample_events.jsonl not found at {sample_events_path}. Skipping.")
        return
        
    logger.info(f"Loading sample events from {sample_events_path}...")
    count = 0
    with open(sample_events_path, "r") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                
                # Check fields and map to database Event model
                event_type = data.get("event_type")
                if not event_type:
                    continue
                    
                # Map keys dynamically
                store_id = data.get("store_id") or data.get("store_code") or "store1"
                camera_id = data.get("camera_id") or "cam1"
                customer_id = data.get("customer_id") or data.get("id_token") or f"cust_{data.get('track_id', 999)}"
                timestamp = data.get("timestamp") or data.get("event_timestamp") or data.get("event_time") or data.get("queue_join_ts")
                
                # If timestamp is missing, skip
                if not timestamp:
                    continue
                    
                # Store extra columns in extra_data
                extra = {k: v for k, v in data.items() if k not in ("event_type", "store_id", "store_code", "camera_id", "customer_id", "id_token", "track_id", "timestamp", "event_timestamp", "event_time")}
                
                event_id = f"evt_sample_{uuid.uuid4().hex[:8]}"
                create_event(
                    db=db,
                    event_id=event_id,
                    store_id=store_id,
                    camera_id=camera_id,
                    customer_id=customer_id,
                    event_type=event_type,
                    timestamp=timestamp,
                    extra_data=extra
                )
                count += 1
            except Exception as e:
                logger.error(f"Error loading sample event line: {e}")
                
    logger.info(f"Loaded {count} sample events into database.")

def run_simulation(db: Session, transactions_csv_path: str):
    """
    Generate transaction-aligned customer events to populate historical metrics.
    Seeds Store 1 and Store 2 metrics to be temporally consistent.
    """
    if not os.path.exists(transactions_csv_path):
        transactions_csv_path = os.path.join("d:/Desktop/purple_round2", transactions_csv_path)
        
    if not os.path.exists(transactions_csv_path):
        logger.warning(f"Transactions CSV not found at {transactions_csv_path}. Simulation skipped.")
        return
        
    logger.info("Running simulation of visitor events matching POS transactions...")
    
    # 1. Read POS Transactions
    transactions = []
    with open(transactions_csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            transactions.append(row)
            
    if not transactions:
        logger.warning("No transactions read from CSV. Simulation skipped.")
        return
        
    # Set seed for reproducibility
    random.seed(42)
    
    # 2. Simulate for each transaction
    # Since transactions only mention ST1008 (Store 2), we will map some to Store 1 and some to Store 2
    # to populate both. E.g. odd orders -> Store 1, even orders -> Store 2.
    # We will insert duplicate transactions with store_id = 'ST1076' (Store 1) to make it have sales as well!
    
    sim_events_count = 0
    cust_sim_id = 2000
    
    for idx, tx in enumerate(transactions):
        order_id = int(tx["order_id"])
        o_date = tx["order_date"].strip()
        o_time = tx["order_time"].strip()
        total_amount = float(tx["total_amount"])
        brand = tx["brand_name"].strip()
        prod_id = tx["product_id"].strip()
        
        # Decide store
        if idx % 2 == 0:
            store_id = "store2"
            db_store_id = "ST1008"
            entry_cam = "entry 1"
            billing_cam = "billing_area"
            zone_cam = "zone"
            shelves = ["makeup_shelf", "skincare_shelf"]
        else:
            store_id = "store1"
            db_store_id = "ST1076"
            entry_cam = "CAM 3 - entry"
            billing_cam = "CAM 5 - billing"
            zone_cam = "CAM 1 - zone"
            shelves = ["shelf_left", "shelf_center"]
            
        # Create corresponding transaction in database (translating store code)
        create_transaction(
            db=db,
            order_id=order_id,
            order_date=o_date,
            order_time=o_time,
            store_id=db_store_id,
            product_id=prod_id,
            brand_name=brand,
            total_amount=total_amount
        )
        
        # Determine order transaction datetime
        tx_dt = parse_datetime(o_date, o_time)
        
        # Create corresponding customer profile
        c_id = f"cust_sim_{cust_sim_id}"
        cust_sim_id += 1
        
        # Sequence of visitor events leading up to this transaction:
        # A. Customer entered store
        dwell_mins = random.randint(15, 40)
        entry_dt = tx_dt - timedelta(minutes=dwell_mins)
        
        create_event(
            db=db,
            event_id=f"evt_sim_entry_{order_id}",
            store_id=store_id,
            camera_id=entry_cam,
            customer_id=c_id,
            event_type="customer_entered",
            timestamp=entry_dt.isoformat() + "Z"
        )
        sim_events_count += 1
        
        # B. Customer visited a shelf zone
        shelf_enter = entry_dt + timedelta(minutes=random.randint(2, 5))
        shelf_dwell = random.randint(180, 500)
        shelf_exit = shelf_enter + timedelta(seconds=shelf_dwell)
        selected_shelf = random.choice(shelves)
        
        create_event(
            db=db,
            event_id=f"evt_sim_zi_{order_id}",
            store_id=store_id,
            camera_id=zone_cam,
            customer_id=c_id,
            event_type="zone_entered",
            timestamp=shelf_enter.isoformat() + "Z",
            extra_data={"roi_id": selected_shelf, "roi_name": selected_shelf.replace("_", " ").title()}
        )
        create_event(
            db=db,
            event_id=f"evt_sim_ze_{order_id}",
            store_id=store_id,
            camera_id=zone_cam,
            customer_id=c_id,
            event_type="zone_exited",
            timestamp=shelf_exit.isoformat() + "Z",
            extra_data={"roi_id": selected_shelf, "roi_name": selected_shelf.replace("_", " ").title()}
        )
        sim_events_count += 2
        
        # C. Customer joined billing queue
        queue_dwell = random.randint(60, 300)
        queue_start = tx_dt - timedelta(seconds=queue_dwell)
        
        create_event(
            db=db,
            event_id=f"evt_sim_qs_{order_id}",
            store_id=store_id,
            camera_id=billing_cam,
            customer_id=c_id,
            event_type="billing_started",
            timestamp=queue_start.isoformat() + "Z"
        )
        
        # D. Customer served and completed billing
        create_event(
            db=db,
            event_id=f"evt_sim_qsv_{order_id}",
            store_id=store_id,
            camera_id=billing_cam,
            customer_id=c_id,
            event_type="billing_serving",
            timestamp=(tx_dt - timedelta(seconds=10)).isoformat() + "Z"
        )
        create_event(
            db=db,
            event_id=f"evt_sim_qc_{order_id}",
            store_id=store_id,
            camera_id=billing_cam,
            customer_id=c_id,
            event_type="billing_completed",
            timestamp=tx_dt.isoformat() + "Z"
        )
        sim_events_count += 3
        
        # E. Customer exited store
        exit_dt = tx_dt + timedelta(minutes=random.randint(1, 2))
        create_event(
            db=db,
            event_id=f"evt_sim_exit_{order_id}",
            store_id=store_id,
            camera_id=entry_cam,
            customer_id=c_id,
            event_type="customer_exited",
            timestamp=exit_dt.isoformat() + "Z"
        )
        sim_events_count += 1
        
    # 3. Simulate non-buyer entries (to keep conversion rate realistic, say 40%)
    # Total buyers is cust_sim_id - 2000. Let's add 1.5x non-buyers
    num_buyers = cust_sim_id - 2000
    num_non_buyers = int(num_buyers * 1.5)
    
    for i in range(num_non_buyers):
        c_id = f"cust_sim_nb_{i}"
        
        # Pick random store and date/hour from transactions bounds (10-04-2026, 9am to 9pm)
        store_id = random.choice(["store1", "store2"])
        entry_cam = "CAM 3 - entry" if store_id == "store1" else "entry 1"
        zone_cam = "CAM 1 - zone" if store_id == "store1" else "zone"
        shelves = ["shelf_left", "shelf_center"] if store_id == "store1" else ["makeup_shelf", "skincare_shelf"]
        
        # Generate random time on 10-04-2026
        rand_hour = random.randint(9, 21)
        rand_min = random.randint(0, 59)
        rand_sec = random.randint(0, 59)
        entry_dt = datetime(2026, 4, 10, rand_hour, rand_min, rand_sec)
        
        # Entered
        create_event(
            db=db,
            event_id=f"evt_sim_nb_ent_{i}",
            store_id=store_id,
            camera_id=entry_cam,
            customer_id=c_id,
            event_type="customer_entered",
            timestamp=entry_dt.isoformat() + "Z"
        )
        sim_events_count += 1
        
        # Zone visit
        shelf_enter = entry_dt + timedelta(minutes=random.randint(2, 5))
        shelf_dwell = random.randint(60, 400)
        selected_shelf = random.choice(shelves)
        create_event(
            db=db,
            event_id=f"evt_sim_nb_zi_{i}",
            store_id=store_id,
            camera_id=zone_cam,
            customer_id=c_id,
            event_type="zone_entered",
            timestamp=shelf_enter.isoformat() + "Z",
            extra_data={"roi_id": selected_shelf, "roi_name": selected_shelf.replace("_", " ").title()}
        )
        create_event(
            db=db,
            event_id=f"evt_sim_nb_ze_{i}",
            store_id=store_id,
            camera_id=zone_cam,
            customer_id=c_id,
            event_type="zone_exited",
            timestamp=(shelf_enter + timedelta(seconds=shelf_dwell)).isoformat() + "Z",
            extra_data={"roi_id": selected_shelf, "roi_name": selected_shelf.replace("_", " ").title()}
        )
        sim_events_count += 2
        
        # Exited
        dwell_mins = random.randint(8, 25)
        exit_dt = entry_dt + timedelta(minutes=dwell_mins)
        create_event(
            db=db,
            event_id=f"evt_sim_nb_ex_{i}",
            store_id=store_id,
            camera_id=entry_cam,
            customer_id=c_id,
            event_type="customer_exited",
            timestamp=exit_dt.isoformat() + "Z"
        )
        sim_events_count += 1
        
    logger.info(f"Simulation completed. Generated {sim_events_count} transaction-aligned visitor events.")
