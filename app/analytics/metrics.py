import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from sqlalchemy.orm import Session

from app.database.models import EventModel, TransactionModel, AlertModel

logger = logging.getLogger("store_intelligence.analytics")

# Normalize store IDs
def normalize_store_id(sid: str) -> str:
    if not sid:
        return ""
    sid_clean = sid.strip().lower()
    if sid_clean in ("store1", "st1076", "store_1076"):
        return "store1"
    if sid_clean in ("store2", "st1008", "store_1008"):
        return "store2"
    return sid_clean

def calculate_store_metrics(db: Session, store_id: str = None) -> dict:
    """
    Calculate retail KPIs for a store or globally.
    KPIs: Footfall, Current Occupancy, Avg Dwell Time, Peak Hour, Queue Length, Avg Queue Time, Revenue, Conversion Rate, Revenue per Visitor.
    """
    events = db.query(EventModel).all()
    transactions = db.query(TransactionModel).all()
    
    if not events:
        return {
            "footfall": 0, "occupancy": 0, "avg_dwell_time_sec": 0.0,
            "peak_hour": "N/A", "queue_length": 0, "avg_queue_time_sec": 0.0,
            "revenue": 0.0, "revenue_per_visitor": 0.0, "conversion_rate": 0.0
        }
        
    # Convert events to DataFrame
    df_evt = pd.DataFrame([{
        "store_id": normalize_store_id(e.store_id),
        "camera_id": e.camera_id,
        "customer_id": e.customer_id,
        "event_type": e.event_type,
        "timestamp": e.timestamp,
        "extra_data": json.loads(e.extra_data) if e.extra_data else {}
    } for e in events])
    if not df_evt.empty:
        df_evt["timestamp"] = pd.to_datetime(df_evt["timestamp"], utc=True, errors="coerce")
    
    # Filter by store if provided
    if store_id:
        norm_store_id = normalize_store_id(store_id)
        df_evt = df_evt[df_evt["store_id"] == norm_store_id]
        
    if df_evt.empty:
        return {
            "footfall": 0, "occupancy": 0, "avg_dwell_time_sec": 0.0,
            "peak_hour": "N/A", "queue_length": 0, "avg_queue_time_sec": 0.0,
            "revenue": 0.0, "revenue_per_visitor": 0.0, "conversion_rate": 0.0
        }
        
    # Calculate Footfall (Unique customers entered)
    footfall_df = df_evt[df_evt["event_type"] == "customer_entered"]
    footfall = footfall_df["customer_id"].nunique()
    
    # Current Occupancy: customers entered but not exited
    entered_custs = set(df_evt[df_evt["event_type"] == "customer_entered"]["customer_id"])
    exited_custs = set(df_evt[df_evt["event_type"] == "customer_exited"]["customer_id"])
    occupancy = len(entered_custs - exited_custs)
    if occupancy < 0:
        occupancy = 0
        
    # Average Dwell Time: time delta between entry and exit per customer
    dwell_times = []
    cust_groups = df_evt[df_evt["event_type"].isin(["customer_entered", "customer_exited"])].groupby("customer_id")
    for cust_id, group in cust_groups:
        entries = group[group["event_type"] == "customer_entered"]["timestamp"]
        exits = group[group["event_type"] == "customer_exited"]["timestamp"]
        if not entries.empty and not exits.empty:
            # Match first entry and last exit (or closest pair)
            entry_t = entries.min()
            exit_t = exits.max()
            if exit_t > entry_t:
                dwell_times.append((exit_t - entry_t).total_seconds())
    avg_dwell_time = np.mean(dwell_times) if dwell_times else 0.0
    
    # Peak Hour: group entry events by hour
    peak_hour = "N/A"
    if not footfall_df.empty:
        footfall_df = footfall_df.copy()
        footfall_df["hour"] = footfall_df["timestamp"].dt.hour
        peak_hour_val = footfall_df["hour"].mode()
        if not peak_hour_val.empty:
            peak_hour = f"{int(peak_hour_val[0])}:00 - {int(peak_hour_val[0])+1}:00"
            
    # Queue waiting calculations
    # Queue length is active customers in billing_started but not billing_completed/exited wait
    billing_started_custs = set(df_evt[df_evt["event_type"] == "billing_started"]["customer_id"])
    billing_served_custs = set(df_evt[df_evt["event_type"].isin(["billing_completed", "billing_serving"])]["customer_id"])
    queue_length = len(billing_started_custs - billing_served_custs)
    if queue_length < 0:
        queue_length = 0
        
    # Average Queue Wait Time (billing_started to billing_serving)
    queue_waits = []
    billing_groups = df_evt[df_evt["event_type"].isin(["billing_started", "billing_serving"])].groupby("customer_id")
    for cust_id, group in billing_groups:
        starts = group[group["event_type"] == "billing_started"]["timestamp"]
        serves = group[group["event_type"] == "billing_serving"]["timestamp"]
        if not starts.empty and not serves.empty:
            start_t = starts.min()
            serve_t = serves.min()
            if serve_t > start_t:
                queue_waits.append((serve_t - start_t).total_seconds())
    avg_queue_time = np.mean(queue_waits) if queue_waits else 0.0
    
    # Transactions Analytics
    if transactions:
        df_tx = pd.DataFrame([{
            "order_id": tx.order_id,
            "store_id": normalize_store_id(tx.store_id),
            "total_amount": tx.total_amount,
            "brand_name": tx.brand_name,
            "product_id": tx.product_id
        } for tx in transactions])
        
        if store_id:
            df_tx = df_tx[df_tx["store_id"] == norm_store_id]
            
        revenue = float(df_tx["total_amount"].sum()) if not df_tx.empty else 0.0
        purchases = df_tx["order_id"].nunique() if not df_tx.empty else 0
    else:
        revenue = 0.0
        purchases = 0
        
    # Ratios
    # If no video entries but we have transaction orders (e.g. historical data), handle it gracefully
    visitor_count = footfall if footfall > 0 else purchases
    
    revenue_per_visitor = revenue / visitor_count if visitor_count > 0 else 0.0
    conversion_rate = purchases / visitor_count if visitor_count > 0 else 0.0
    if conversion_rate > 1.0:
        conversion_rate = 1.0 # cap conversion rate at 100%
        
    return {
        "footfall": int(footfall),
        "occupancy": int(occupancy),
        "avg_dwell_time_sec": float(round(avg_dwell_time, 2)),
        "peak_hour": peak_hour,
        "queue_length": int(queue_length),
        "avg_queue_time_sec": float(round(avg_queue_time, 2)),
        "revenue": float(round(revenue, 2)),
        "revenue_per_visitor": float(round(revenue_per_visitor, 2)),
        "conversion_rate": float(round(conversion_rate, 4))
    }

def calculate_revenue_analytics(db: Session, store_id: str = None) -> dict:
    """Calculate deep revenue stats: revenue by store, brand, hour, top selling products."""
    transactions = db.query(TransactionModel).all()
    if not transactions:
        return {
            "revenue_by_store": {}, "revenue_by_brand": {},
            "revenue_by_hour": {}, "top_products": []
        }
        
    df_tx = pd.DataFrame([{
        "order_id": tx.order_id,
        "order_date": tx.order_date,
        "order_time": tx.order_time,
        "store_id": normalize_store_id(tx.store_id),
        "product_id": tx.product_id,
        "brand_name": tx.brand_name,
        "total_amount": tx.total_amount
    } for tx in transactions])
    
    if store_id:
        norm_store_id = normalize_store_id(store_id)
        df_tx = df_tx[df_tx["store_id"] == norm_store_id]
        
    if df_tx.empty:
        return {
            "revenue_by_store": {}, "revenue_by_brand": {},
            "revenue_by_hour": {}, "top_products": []
        }
        
    # Group Revenue by Store
    rev_by_store = df_tx.groupby("store_id")["total_amount"].sum().round(2).to_dict()
    
    # Group Revenue by Brand
    rev_by_brand = df_tx.groupby("brand_name")["total_amount"].sum().sort_values(ascending=False).round(2).to_dict()
    
    # Group Revenue by Hour
    # Extract hour from order_time (format: HH:MM:SS)
    df_tx = df_tx.copy()
    df_tx["hour"] = df_tx["order_time"].apply(lambda x: int(x.split(":")[0]) if isinstance(x, str) and ":" in x else 0)
    rev_by_hour = df_tx.groupby("hour")["total_amount"].sum().round(2).to_dict()
    
    # Top Selling Products (grouped by product_id)
    top_prod_df = df_tx.groupby(["product_id", "brand_name"]).agg(
        sales_count=("order_id", "count"),
        total_revenue=("total_amount", "sum")
    ).reset_index().sort_values(by="sales_count", ascending=False).head(10)
    
    top_products = top_prod_df.round(2).to_dict(orient="records")
    
    return {
        "revenue_by_store": rev_by_store,
        "revenue_by_brand": rev_by_brand,
        "revenue_by_hour": {str(k): v for k, v in rev_by_hour.items()},
        "top_products": top_products
    }

def calculate_zone_analytics(db: Session, store_id: str = None) -> dict:
    """Calculate zone engagement details: visit counts and average dwell times per shelf zone."""
    events = db.query(EventModel).all()
    if not events:
        return {}
        
    df_evt = pd.DataFrame([{
        "store_id": normalize_store_id(e.store_id),
        "customer_id": e.customer_id,
        "event_type": e.event_type,
        "timestamp": e.timestamp,
        "extra_data": json.loads(e.extra_data) if e.extra_data else {}
    } for e in events])
    if not df_evt.empty:
        df_evt["timestamp"] = pd.to_datetime(df_evt["timestamp"], utc=True, errors="coerce")
    
    if store_id:
        norm_store_id = normalize_store_id(store_id)
        df_evt = df_evt[df_evt["store_id"] == norm_store_id]
        
    df_zone_events = df_evt[df_evt["event_type"].isin(["zone_entered", "zone_exited"])]
    if df_zone_events.empty:
        return {}
        
    df_zone_events = df_zone_events.copy()
    df_zone_events["zone_name"] = df_zone_events["extra_data"].apply(lambda x: x.get("roi_name", "Unknown"))
    
    # Calculate Popularity (number of zone_entered events)
    popularity = df_zone_events[df_zone_events["event_type"] == "zone_entered"]["zone_name"].value_counts().to_dict()
    
    # Calculate Average Dwell Time per zone
    zone_dwells = {} # zone_name -> list of seconds
    
    # Group by customer and zone
    grouped = df_zone_events.groupby(["customer_id", "zone_name"])
    for (cust_id, zone_name), group in grouped:
        enters = group[group["event_type"] == "zone_entered"]["timestamp"]
        exits = group[group["event_type"] == "zone_exited"]["timestamp"]
        if not enters.empty and not exits.empty:
            # Sort and match pairs
            ent_list = sorted(enters.tolist())
            ex_list = sorted(exits.tolist())
            for ent_t in ent_list:
                # Find the first exit after this entry
                matching_exits = [ex for ex in ex_list if ex > ent_t]
                if matching_exits:
                    duration = (matching_exits[0] - ent_t).total_seconds()
                    zone_dwells.setdefault(zone_name, []).append(duration)
                    
    avg_dwell_times = {
        z_name: float(round(np.mean(dwells), 2))
        for z_name, dwells in zone_dwells.items() if dwells
    }
    
    # Formatting output
    zone_stats = {}
    all_zones = set(popularity.keys()).union(set(avg_dwell_times.keys()))
    for z in all_zones:
        zone_stats[z] = {
            "visits": int(popularity.get(z, 0)),
            "avg_dwell_time_sec": float(avg_dwell_times.get(z, 0.0))
        }
        
    return zone_stats
