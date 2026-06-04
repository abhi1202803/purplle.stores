import os
import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Depends, Query, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("store_intelligence.api")

from app.database.connection import get_db, Base, engine
from app.database.models import EventModel
from app.database.crud import init_db, import_transactions_from_csv, get_events, get_transactions, get_alerts
from app.database.simulator import populate_sample_events, run_simulation
from app.analytics.metrics import calculate_store_metrics, calculate_revenue_analytics, calculate_zone_analytics, normalize_store_id
from app.analytics.heatmaps import generate_store_heatmaps
from app.anomaly_detection.detector import run_rule_based_detection, train_and_detect_isolation_forest
from app.event_generation.processor import run_store_pipeline

app = FastAPI(
    title="Purplle Store Intelligence System REST API",
    description="Production-ready REST API for computer-vision and transactional retail intelligence.",
    version="1.0.0"
)

# Enable CORS for Streamlit
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def run_video_processing_in_background():
    """Background task to process Store 1 and Store 2 videos."""
    db = next(get_db())
    try:
        # Process Store 1
        run_store_pipeline("store1", "data/store1", db, frame_skip=15)
        # Process Store 2
        run_store_pipeline("store2", "data/store2", db, frame_skip=15)
        
        # Trigger anomaly detection on video data
        run_rule_based_detection(db, "store1")
        run_rule_based_detection(db, "store2")
        train_and_detect_isolation_forest(db, "store1")
        train_and_detect_isolation_forest(db, "store2")
        
        # Pre-generate heatmaps
        generate_store_heatmaps(db, "store1")
        generate_store_heatmaps(db, "store2")
        
        logger.info("Background video processing and analysis complete.")
    except Exception as e:
        logger.error(f"Error in background video processing: {e}", exc_info=True)
    finally:
        db.close()

@app.on_event("startup")
def startup_event():
    """Run database setup, transactional imports, and trigger asynchronous video processing."""
    # 1. Initialize Tables
    init_db()
    
    db = next(get_db())
    try:
        # 2. Import POS transactions if database is empty
        # We check if transaction counts are 0
        from app.database.models import TransactionModel, EventModel
        tx_count = db.query(TransactionModel).count()
        if tx_count == 0:
            import_transactions_from_csv(db, "data/transactions/POS_transactions.csv")
            
        # 3. Load sample events & run simulation if event counts are 0
        evt_count = db.query(EventModel).count()
        if evt_count == 0:
            populate_sample_events(db, "data/sample_events.jsonl")
            run_simulation(db, "data/transactions/POS_transactions.csv")
            
            # Run initial anomalies detection on simulated data
            run_rule_based_detection(db, "store1")
            run_rule_based_detection(db, "store2")
            train_and_detect_isolation_forest(db, "store1")
            train_and_detect_isolation_forest(db, "store2")
            
            # Pre-generate heatmaps
            generate_store_heatmaps(db, "store1")
            generate_store_heatmaps(db, "store2")
            
        # 4. Trigger video processing asynchronously
        # Using simple threading to avoid blocking startup
        import threading
        t = threading.Thread(target=run_video_processing_in_background)
        t.daemon = True
        t.start()
        logger.info("Asynchronous video processing pipeline triggered.")
        
    except Exception as e:
        logger.error(f"Error in database/simulation startup: {e}", exc_info=True)
    finally:
        db.close()

@app.get("/health", tags=["System"])
def get_health(db: Session = Depends(get_db)):
    """Health check endpoint to verify API server and DB connectivity."""
    try:
        # Execute basic query
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_ok = False
        
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

@app.get("/metrics", tags=["Analytics"])
def get_global_metrics(db: Session = Depends(get_db)):
    """Retrieve global KPIs for all stores combined."""
    metrics = calculate_store_metrics(db)
    return metrics

@app.get("/metrics/{store_id}", tags=["Analytics"])
def get_store_metrics(store_id: str, db: Session = Depends(get_db)):
    """Retrieve KPIs filtered for a specific store (store1 or store2)."""
    norm_id = normalize_store_id(store_id)
    if norm_id not in ("store1", "store2"):
        raise HTTPException(status_code=404, detail=f"Store '{store_id}' not found. Use 'store1' or 'store2'.")
    metrics = calculate_store_metrics(db, store_id=norm_id)
    return metrics

@app.get("/events", tags=["Data"])
def get_all_events(
    store_id: Optional[str] = None,
    camera_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """Retrieve event logs, with optional filtering by store, camera, and event type."""
    norm_store_id = normalize_store_id(store_id) if store_id else None
    events = get_events(db, store_id=norm_store_id, camera_id=camera_id, event_type=event_type, limit=limit)
    
    # Format events to dictionary
    result = []
    for e in events:
        import json
        result.append({
            "event_id": e.event_id,
            "store_id": e.store_id,
            "camera_id": e.camera_id,
            "customer_id": e.customer_id,
            "event_type": e.event_type,
            "timestamp": e.timestamp,
            "extra_data": json.loads(e.extra_data) if e.extra_data else {}
        })
    return result

@app.get("/zones", tags=["Analytics"])
def get_zones_analytics(store_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Retrieve popularity and dwell times for shelf zones."""
    norm_store_id = normalize_store_id(store_id) if store_id else None
    return calculate_zone_analytics(db, store_id=norm_store_id)

@app.get("/heatmaps", tags=["Analytics"])
def get_heatmap_image(store_id: str, heatmap_type: str = "movement", db: Session = Depends(get_db)):
    """
    Retrieve generated heatmap overlays (movement or engagement) as an image.
    :param store_id: 'store1' or 'store2'
    :param heatmap_type: 'movement' or 'engagement'
    """
    norm_id = normalize_store_id(store_id)
    if norm_id not in ("store1", "store2"):
        raise HTTPException(status_code=404, detail="Store not found.")
        
    if heatmap_type not in ("movement", "engagement"):
        raise HTTPException(status_code=400, detail="Invalid heatmap_type. Choose 'movement' or 'engagement'.")
        
    img_path = f"outputs/heatmaps/{norm_id}_{heatmap_type}.png"
    if not os.path.exists(img_path):
        # Generate on-the-fly if missing
        generate_store_heatmaps(db, norm_id)
        
    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Heatmap image could not be loaded/generated.")
        
    return FileResponse(img_path, media_type="image/png")

@app.get("/anomalies", tags=["Security / Operations"])
def get_anomalies_alerts(store_id: Optional[str] = None, limit: int = Query(50, ge=1), db: Session = Depends(get_db)):
    """Retrieve security and operational alerts generated by rule-based monitoring and Isolation Forest."""
    norm_store_id = normalize_store_id(store_id) if store_id else None
    alerts = get_alerts(db, store_id=norm_store_id, limit=limit)
    return [{
        "alert_id": a.alert_id,
        "store_id": a.store_id,
        "alert_type": a.alert_type,
        "description": a.description,
        "timestamp": a.timestamp,
        "severity": a.severity
    } for a in alerts]

@app.get("/stores", tags=["System"])
def get_stores_occupancy(db: Session = Depends(get_db)):
    """List stores and their current active customer occupancy."""
    m1 = calculate_store_metrics(db, store_id="store1")
    m2 = calculate_store_metrics(db, store_id="store2")
    return [
        {"store_id": "store1", "name": "Store 1 (Phoenix Mall)", "current_occupancy": m1["occupancy"]},
        {"store_id": "store2", "name": "Store 2 (High Street)", "current_occupancy": m2["occupancy"]}
    ]

@app.get("/revenue", tags=["POS Integration"])
def get_revenue_analytics(store_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Retrieve sales analytics including store revenue, brand sales, hourly revenue, and best-selling products."""
    norm_store_id = normalize_store_id(store_id) if store_id else None
    return calculate_revenue_analytics(db, store_id=norm_store_id)

@app.get("/conversion", tags=["POS Integration"])
def get_conversion_analytics(store_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Retrieve transaction conversion rates and revenue-per-visitor metrics."""
    norm_store_id = normalize_store_id(store_id) if store_id else None
    metrics = calculate_store_metrics(db, store_id=norm_store_id)
    return {
        "store_id": store_id or "global",
        "conversion_rate": metrics["conversion_rate"],
        "revenue": metrics["revenue"],
        "footfall": metrics["footfall"],
        "revenue_per_visitor": metrics["revenue_per_visitor"]
    }

@app.get("/funnel", tags=["Analytics"])
def get_funnel_analytics(store_id: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Retrieve session-based customer conversion funnel steps showing drop-off behavior:
    1. Entered (Footfall) -> 2. Engaged (Visited Shelf) -> 3. Checkout (Started Billing) -> 4. Purchased (Completed Billing).
    """
    norm_store_id = normalize_store_id(store_id) if store_id else None
    
    # Query all events
    query = db.query(EventModel)
    if norm_store_id:
        query = query.filter(EventModel.store_id == norm_store_id)
        
    events = query.all()
    
    if not events:
        return {
            "steps": [
                {"stage": "1. Entered (Footfall)", "count": 0, "percentage": 0.0},
                {"stage": "2. Engaged (Browsed Shelves)", "count": 0, "percentage": 0.0},
                {"stage": "3. Checkout (Billing Line)", "count": 0, "percentage": 0.0},
                {"stage": "4. Purchased (Completed)", "count": 0, "percentage": 0.0}
            ],
            "dropoffs": []
        }
        
    # Aggregate counts of unique customers at each stage
    entered_custs = set()
    engaged_custs = set()
    checkout_custs = set()
    purchased_custs = set()
    
    for e in events:
        cid = e.customer_id
        etype = e.event_type
        if etype == "customer_entered":
            entered_custs.add(cid)
        elif etype == "zone_entered":
            # Exclude billing zones from engaged steps to avoid funnel inflation
            roi_name = ""
            if e.extra_data:
                try:
                    import json
                    extra = json.loads(e.extra_data)
                    roi_name = extra.get("roi_name", "").lower()
                except:
                    pass
            if "billing" not in roi_name and "queue" not in roi_name:
                engaged_custs.add(cid)
        elif etype == "billing_started":
            checkout_custs.add(cid)
        elif etype == "billing_completed":
            purchased_custs.add(cid)
            
    # Session-based alignment: ensure subsequent steps only contain customers who entered
    engaged_custs = engaged_custs.intersection(entered_custs)
    checkout_custs = checkout_custs.intersection(entered_custs)
    purchased_custs = purchased_custs.intersection(entered_custs)
    
    # Enforce standard funnel logic: step N must be a subset of step N-1
    c_entered = len(entered_custs)
    c_engaged = len(engaged_custs)
    c_checkout = len(checkout_custs.intersection(engaged_custs))
    c_purchased = len(purchased_custs.intersection(checkout_custs).intersection(engaged_custs))
    
    # Fallback to prevent 0 count if entry wasn't recorded but later events exist
    if c_entered == 0:
        c_entered = max(c_engaged, c_checkout, c_purchased)
        
    p_entered = 100.0 if c_entered > 0 else 0.0
    p_engaged = (c_engaged / c_entered * 100.0) if c_entered > 0 else 0.0
    p_checkout = (c_checkout / c_entered * 100.0) if c_entered > 0 else 0.0
    p_purchased = (c_purchased / c_entered * 100.0) if c_entered > 0 else 0.0
    
    # Calculate drop-off percentages between consecutive steps
    dropoff_browse = ((c_entered - c_engaged) / c_entered * 100.0) if c_entered > 0 else 0.0
    dropoff_checkout = ((c_engaged - c_checkout) / c_engaged * 100.0) if c_engaged > 0 else 0.0
    dropoff_purchase = ((c_checkout - c_purchased) / c_checkout * 100.0) if c_checkout > 0 else 0.0
    
    return {
        "store_id": store_id or "global",
        "steps": [
            {"stage": "1. Entered (Footfall)", "count": c_entered, "percentage": round(p_entered, 2)},
            {"stage": "2. Engaged (Browsed Shelves)", "count": c_engaged, "percentage": round(p_engaged, 2)},
            {"stage": "3. Checkout (Billing Line)", "count": c_checkout, "percentage": round(p_checkout, 2)},
            {"stage": "4. Purchased (Completed)", "count": c_purchased, "percentage": round(p_purchased, 2)}
        ],
        "dropoffs": [
            {"stage": "Entry to Engagement Dropoff", "percentage": round(dropoff_browse, 2)},
            {"stage": "Engagement to Checkout Dropoff", "percentage": round(dropoff_checkout, 2)},
            {"stage": "Checkout to Purchase Dropoff", "percentage": round(dropoff_purchase, 2)}
        ]
    }

@app.websocket("/ws/logs")
async def websocket_logs_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket logs subscriber connected.")
    try:
        # Send first log immediately
        now = datetime.now()
        time_str = now.strftime("%I:%M:%S %p").lower()
        if time_str.startswith("0"):
            time_str = time_str[1:]
        await websocket.send_text(f"{time_str} live_metrics")
        
        while True:
            await asyncio.sleep(10)
            now = datetime.now()
            time_str = now.strftime("%I:%M:%S %p").lower()
            if time_str.startswith("0"):
                time_str = time_str[1:]
            await websocket.send_text(f"{time_str} live_metrics")
    except WebSocketDisconnect:
        logger.info("WebSocket logs subscriber disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

