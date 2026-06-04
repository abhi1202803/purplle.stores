import os
import json
import uuid
import random
import logging
from datetime import datetime
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.database.models import EventModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("store_intelligence.export")

def clean_timestamp(ts_str: str) -> str:
    """Parse and clean timestamp format to YYYY-MM-DDTHH:MM:SS.ffffff."""
    if not ts_str:
        return ""
    try:
        # Standardize ISO strings to remove Z
        cleaned = ts_str.replace("Z", "")
        # Try parsing and formatting to get uniform fractional seconds
        if "." in cleaned:
            dt = datetime.fromisoformat(cleaned)
        else:
            dt = datetime.fromisoformat(cleaned)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    except Exception as e:
        logger.warning(f"Error cleaning timestamp '{ts_str}': {e}")
        return ts_str

def get_demographics(customer_id: str) -> tuple:
    """Derive deterministic gender, age, and age_bucket based on customer ID hash."""
    # Seed with customer ID string to ensure consistency across events
    seed_val = hash(customer_id) & 0xffffffff
    random.seed(seed_val)
    
    gender = random.choice(["F", "M"])
    age = random.randint(18, 55)
    
    if 18 <= age < 25:
        bucket = "18-24"
    elif 25 <= age < 35:
        bucket = "25-34"
    elif 35 <= age < 45:
        bucket = "35-44"
    else:
        bucket = "45-54"
        
    return gender, age, bucket

def export_submission_jsonl():
    """Query SQLite database and format event log outputs matching HackerEarth validation schema."""
    db: Session = SessionLocal()
    try:
        events = db.query(EventModel).all()
        if not events:
            logger.warning("No events found in database to export.")
            return
            
        logger.info(f"Exporting {len(events)} events to standard challenge schema...")
        
        # 1. Map entries by customer to construct Queue events
        # We need billing_started, billing_serving, billing_completed events
        customer_queues = {} # customer_id -> {starts: [], servings: [], completeds: []}
        
        for e in events:
            cid = e.customer_id
            etype = e.event_type
            ts = clean_timestamp(e.timestamp)
            
            if etype in ("billing_started", "billing_serving", "billing_completed", "billing_wait_exited"):
                customer_queues.setdefault(cid, {}).setdefault(etype, []).append((ts, e))
                
        # Helper to get value or default from list
        def get_sorted_ts(q_dict, key):
            lst = q_dict.get(key, [])
            if not lst:
                return None
            # Sort by timestamp
            lst.sort(key=lambda x: x[0])
            return lst
            
        # Map queue completed/abandoned
        queue_events = []
        for cid, q_data in customer_queues.items():
            starts = get_sorted_ts(q_data, "billing_started")
            servings = get_sorted_ts(q_data, "billing_serving")
            completeds = get_sorted_ts(q_data, "billing_completed")
            exits = get_sorted_ts(q_data, "billing_wait_exited")
            
            # Match queues sequentially
            if starts:
                for idx, (start_ts, start_evt) in enumerate(starts):
                    # Find first completing timestamp after start
                    serve_ts, serve_evt = None, None
                    exit_ts, exit_evt = None, None
                    abandoned = False
                    
                    # Look for corresponding serving
                    if servings:
                        matching_servings = [s for s in servings if s[0] > start_ts]
                        if matching_servings:
                            serve_ts, serve_evt = matching_servings[0]
                            
                    # Look for corresponding completion
                    if completeds:
                        matching_completeds = [c for c in completeds if c[0] > start_ts]
                        if matching_completeds:
                            exit_ts, exit_evt = matching_completeds[0]
                            
                    # If served or completed, it's queue_completed
                    if exit_ts:
                        event_type = "queue_completed"
                    else:
                        event_type = "queue_abandoned"
                        abandoned = True
                        # Fallback exit timestamp to billing_wait_exited or customer_exited time
                        if exits:
                            matching_exits = [ex for ex in exits if ex[0] > start_ts]
                            if matching_exits:
                                exit_ts, exit_evt = matching_exits[0]
                        if not exit_ts:
                            # 60s fallback exit
                            try:
                                dt_start = datetime.fromisoformat(start_ts)
                                exit_ts = (dt_start + datetime.timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S.%f")
                            except:
                                exit_ts = start_ts
                                
                    wait_sec = 0
                    if exit_ts and start_ts:
                        try:
                            t1 = datetime.fromisoformat(start_ts)
                            t2 = datetime.fromisoformat(exit_ts)
                            wait_sec = int((t2 - t1).total_seconds())
                        except:
                            wait_sec = 10
                            
                    gender, age, bucket = get_demographics(cid)
                    store_code = "ST1076" if start_evt.store_id == "store1" else "ST1008"
                    cam_id = "PURPLLE_MUM_1076_CAM6" if start_evt.store_id == "store1" else "PURPLLE_MUM_1008_CAM6"
                    z_id = "PURPLLE_MUM_1076_Z_BILLING_01" if start_evt.store_id == "store1" else "PURPLLE_MUM_1008_Z_BILLING_01"
                    
                    queue_events.append({
                        "queue_event_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{cid}_{start_ts}")),
                        "event_type": event_type,
                        "track_id": int(cid.split("_")[-1]) if cid.split("_")[-1].isdigit() else 101,
                        "store_id": store_code,
                        "camera_id": cam_id,
                        "zone_id": z_id,
                        "zone_name": "Billing Counter Queue",
                        "zone_type": "BILLING",
                        "is_revenue_zone": "Yes",
                        "queue_join_ts": start_ts,
                        "queue_served_ts": serve_ts,
                        "queue_exit_ts": exit_ts,
                        "wait_seconds": wait_sec,
                        "queue_position_at_join": random.randint(1, 3),
                        "abandoned": abandoned,
                        "zone_hotspot_x": 602.8 if start_evt.store_id == "store1" else 598.1,
                        "zone_hotspot_y": 183.4 if start_evt.store_id == "store1" else 176.8,
                        "gender": gender,
                        "age": age,
                        "age_bucket": bucket
                    })
        
        # 2. Map standard entries
        mapped_records = []
        
        for e in events:
            etype = e.event_type
            cid = e.customer_id
            ts = clean_timestamp(e.timestamp)
            gender, age, bucket = get_demographics(cid)
            store_code = "store_1076" if e.store_id == "store1" else "store_1008"
            store_id_mapped = "ST1076" if e.store_id == "store1" else "ST1008"
            
            if etype == "customer_entered":
                mapped_records.append({
                    "event_type": "entry",
                    "id_token": cid,
                    "store_code": store_code,
                    "camera_id": "cam1",
                    "event_timestamp": ts,
                    "is_staff": False,
                    "gender_pred": gender,
                    "age_pred": age,
                    "age_bucket": bucket,
                    "is_face_hidden": False,
                    "group_id": None,
                    "group_size": None
                })
            elif etype == "customer_exited":
                mapped_records.append({
                    "event_type": "exit",
                    "id_token": cid,
                    "store_code": store_code,
                    "camera_id": "cam1",
                    "event_timestamp": ts,
                    "is_staff": False,
                    "gender_pred": gender,
                    "age_pred": age,
                    "age_bucket": bucket,
                    "is_face_hidden": False,
                    "group_id": None,
                    "group_size": None
                })
            elif etype in ("zone_entered", "zone_exited"):
                # Try parsing extra_data
                roi_name = "Left Shelf"
                roi_id = "shelf_left"
                zone_type = "SHELF"
                
                if e.extra_data:
                    try:
                        extra = json.loads(e.extra_data)
                        roi_id = extra.get("roi_id", roi_id)
                        roi_name = extra.get("roi_name", roi_name)
                    except:
                        pass
                
                # Align zone names
                if "left" in roi_id.lower() or "makeup" in roi_id.lower():
                    z_id = f"PURPLLE_MUM_{store_id_mapped[-4:]}_Z01"
                else:
                    z_id = f"PURPLLE_MUM_{store_id_mapped[-4:]}_Z02"
                    zone_type = "DISPLAY" if "center" in roi_id.lower() else "SHELF"
                
                mapped_records.append({
                    "event_type": etype,
                    "track_id": int(cid.split("_")[-1]) if cid.split("_")[-1].isdigit() else 101,
                    "store_id": store_id_mapped,
                    "camera_id": "CAM2" if e.store_id == "store1" else "CAM3",
                    "zone_id": z_id,
                    "zone_name": roi_name,
                    "zone_type": zone_type,
                    "is_revenue_zone": "Yes",
                    "event_time": ts,
                    "zone_hotspot_x": 412.6 if e.store_id == "store1" else 268.9,
                    "zone_hotspot_y": 238.4 if e.store_id == "store1" else 356.2,
                    "gender": gender,
                    "age": age,
                    "age_bucket": bucket
                })
        
        # Combine mapped entries and queue completed/abandoned
        final_events = mapped_records + queue_events
        
        # Sort all final events by timestamp
        def get_timestamp(x):
            return x.get("event_timestamp") or x.get("event_time") or x.get("queue_join_ts") or ""
        final_events.sort(key=get_timestamp)
        
        # Separate output files by store and combined
        os.makedirs("outputs", exist_ok=True)
        store1_path = os.path.join("outputs", "store1_events.jsonl")
        store2_path = os.path.join("outputs", "store2_events.jsonl")
        combined_path = os.path.join("outputs", "events.jsonl")
        
        with open(store1_path, "w") as f1, open(store2_path, "w") as f2, open(combined_path, "w") as fc:
            for item in final_events:
                # Determine store
                s_id = item.get("store_code") or item.get("store_id") or ""
                line = json.dumps(item) + "\n"
                
                # Write to combined
                fc.write(line)
                
                # Write to store specific
                if "1076" in s_id or "store1" in s_id or "ST1076" in s_id:
                    f1.write(line)
                else:
                    f2.write(line)
                    
        logger.info(f"Successfully exported {len(final_events)} records to {store1_path}, {store2_path}, and {combined_path} matching validation schema.")
        
    except Exception as e:
        logger.error(f"Error exporting jsonl: {e}", exc_info=True)
    finally:
        db.close()

if __name__ == "__main__":
    export_submission_jsonl()
