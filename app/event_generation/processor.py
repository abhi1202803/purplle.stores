import os
import cv2
import json
import logging
import uuid
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ultralytics import YOLO

from app.config.config import load_store_config, project_point, get_camera_id_from_filename
from app.database.crud import create_event

logger = logging.getLogger("store_intelligence.processor")

class VideoProcessor:
    def __init__(self, video_path: str, store_id: str, frame_skip: int = 15):
        """
        Initialize the VideoProcessor.
        :param video_path: Path to the mp4 file.
        :param store_id: 'store1' or 'store2'.
        :param frame_skip: Skip frames to speed up processing (e.g. 15 skip = ~2 FPS).
        """
        self.video_path = video_path
        self.store_id = store_id
        self.frame_skip = frame_skip
        
        # Load store config and resolve camera
        self.config = load_store_config(store_id)
        self.filename = os.path.basename(video_path)
        self.camera_key = get_camera_id_from_filename(self.filename)
        
        if self.camera_key not in self.config:
            raise KeyError(f"Camera '{self.camera_key}' not found in store configuration.")
            
        self.cam_config = self.config[self.camera_key]
        self.rois = self.cam_config.get("rois", {})
        self.homography = self.cam_config.get("homography")
        
        # Initialize YOLOv11 model (auto-download yolo11n.pt if not present)
        # Using a lock or shared weights in production, local instantiation is fine
        self.model = YOLO("yolo11n.pt")

    def extract_torso_hist(self, frame: np.ndarray, bbox: list) -> np.ndarray:
        """Extract normalized 3D HSV color histogram from the middle torso of a bounding box."""
        h, w, _ = frame.shape
        x1, y1, x2, y2 = map(int, bbox)
        
        # Clip coordinates
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        
        if x2 <= x1 or y2 <= y1:
            return None
            
        box_h = y2 - y1
        # Torso: middle-upper 50% of the box
        torso_y1 = y1 + int(box_h * 0.15)
        torso_y2 = y1 + int(box_h * 0.65)
        
        crop = frame[torso_y1:torso_y2, x1:x2]
        if crop.size == 0:
            return None
            
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        # 8x8x8 bins for HSV
        hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
        cv2.normalize(hist, hist)
        return hist.flatten()

    def check_roi_containment(self, x_center: float, y_bottom: float) -> tuple:
        """Check if a bottom-center point falls inside any configured ROIs. Returns (roi_id, roi_name, roi_type) or None."""
        point = (x_center, y_bottom)
        for r_id, r_info in self.rois.items():
            poly = r_info["polygon"]
            poly_np = np.array(poly, dtype=np.int32)
            # dist >= 0 means inside or on boundary
            if cv2.pointPolygonTest(poly_np, point, False) >= 0:
                return r_id, r_info["name"], r_info["type"]
        return None

    def process(self) -> dict:
        """
        Process the video file. Returns raw local tracks.
        Output tracks dictionary format:
        {
            local_track_id: {
                'camera_id': ...,
                'start_time': datetime,
                'end_time': datetime,
                'points': [(x_layout, y_layout, timestamp)],
                'raw_coords': [(x_cam, y_cam, timestamp)],
                'hists': [hist_array],
                'roi_history': [(roi_id, timestamp)]
            }
        }
        """
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            logger.error(f"Cannot open video file: {self.video_path}")
            return {}
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 25.0
            
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        logger.info(f"Processing camera {self.camera_key} from {self.filename} ({total_frames} frames, {fps} FPS, frame_skip={self.frame_skip})")
        
        # Base timestamp for the videos
        base_time = datetime(2026, 6, 1, 12, 0, 0)
        
        local_tracks = {}
        frame_idx = 0
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
                
            if frame_idx % self.frame_skip != 0:
                frame_idx += 1
                continue
                
            # Frame time delta
            seconds_offset = frame_idx / fps
            timestamp = base_time + timedelta(seconds=seconds_offset)
            
            # Run YOLO tracking (persist=True makes YOLO keep tracking IDs using ByteTrack)
            # classes=[0] is person only
            results = self.model.track(frame, persist=True, classes=[0], tracker="bytetrack.yaml", verbose=False)
            
            if results and results[0].boxes is not None and results[0].boxes.id is not None:
                boxes = results[0].boxes
                track_ids = boxes.id.int().cpu().tolist()
                xyxy = boxes.xyxy.cpu().tolist()
                
                for t_id, bbox in zip(track_ids, xyxy):
                    x1, y1, x2, y2 = bbox
                    x_center = (x1 + x2) / 2.0
                    y_bottom = y2
                    
                    # Project points to layout coords
                    x_proj, y_proj = project_point(self.homography, x_center, y_bottom)
                    
                    # Get torso histogram
                    hist = self.extract_torso_hist(frame, bbox)
                    
                    # Check ROI collision
                    roi_hit = self.check_roi_containment(x_center, y_bottom)
                    
                    # Init track if new
                    if t_id not in local_tracks:
                        local_tracks[t_id] = {
                            "camera_id": self.camera_key,
                            "start_time": timestamp,
                            "end_time": timestamp,
                            "points": [],
                            "raw_coords": [],
                            "hists": [],
                            "roi_history": []
                        }
                        
                    track = local_tracks[t_id]
                    track["end_time"] = timestamp
                    track["points"].append((x_proj, y_proj, timestamp))
                    track["raw_coords"].append((x_center, y_bottom, timestamp))
                    if hist is not None:
                        track["hists"].append(hist)
                    if roi_hit:
                        # Add ROI transition if different from last
                        roi_id = roi_hit[0]
                        if not track["roi_history"] or track["roi_history"][-1][0] != roi_id:
                            track["roi_history"].append((roi_id, timestamp))
            
            frame_idx += 1
            
        cap.release()
        
        # Post-process local tracks: average the histograms to form signature
        for t_id, track in local_tracks.items():
            if track["hists"]:
                track["signature_hist"] = np.mean(track["hists"], axis=0)
            else:
                track["signature_hist"] = None
                
        return local_tracks

class MultiCameraMatcher:
    def __init__(self, sim_threshold: float = 0.6):
        self.sim_threshold = sim_threshold

    def compare_hists(self, hist1: np.ndarray, hist2: np.ndarray) -> float:
        """Compare two color histograms using correlation."""
        if hist1 is None or hist2 is None:
            return 0.0
        h1 = hist1.reshape(8, 8, 8)
        h2 = hist2.reshape(8, 8, 8)
        # cv2.HISTCMP_CORREL returns values between -1 and 1
        return float(cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL))

    def associate(self, all_camera_tracks: dict) -> dict:
        """
        Associate local camera tracks to global customer IDs.
        :param all_camera_tracks: Dictionary mapping camera_id -> local_tracks
        :returns: Map of (camera_id, local_track_id) -> global_customer_id
        """
        # Flatten all tracks into a list sorted by start_time
        flat_tracks = []
        for cam_id, tracks in all_camera_tracks.items():
            for t_id, t_info in tracks.items():
                flat_tracks.append({
                    "camera_id": cam_id,
                    "local_track_id": t_id,
                    "start_time": t_info["start_time"],
                    "end_time": t_info["end_time"],
                    "signature_hist": t_info["signature_hist"]
                })
        
        flat_tracks.sort(key=lambda x: x["start_time"])
        
        global_map = {}
        global_customers = [] # list of dicts: {'customer_id': ..., 'tracks': [track_info], 'signature_hist': ...}
        
        cust_counter = 101
        
        for track in flat_tracks:
            best_sim = -1.0
            best_match_idx = -1
            
            # Search matches in existing global customer profiles
            for idx, cust in enumerate(global_customers):
                # 1. Temporal rule: new track cannot start significantly before last track ends
                # (Allow up to 5 seconds overlap for handoff, and transition must happen within 180s)
                last_track = cust["tracks"][-1]
                time_gap = (track["start_time"] - last_track["end_time"]).total_seconds()
                
                # Check transition window: -5s to +180s
                if -5.0 <= time_gap <= 180.0:
                    # 2. Color Similarity check
                    if track["signature_hist"] is not None and cust["signature_hist"] is not None:
                        sim = self.compare_hists(track["signature_hist"], cust["signature_hist"])
                        if sim > best_sim:
                            best_sim = sim
                            best_match_idx = idx
            
            # Associate to matched profile if similarity exceeds threshold
            if best_match_idx != -1 and best_sim >= self.sim_threshold:
                cust = global_customers[best_match_idx]
                cust["tracks"].append(track)
                # Update signature histogram dynamically as moving average
                if track["signature_hist"] is not None:
                    if cust["signature_hist"] is not None:
                        cust["signature_hist"] = 0.7 * cust["signature_hist"] + 0.3 * track["signature_hist"]
                    else:
                        cust["signature_hist"] = track["signature_hist"]
                
                global_map[(track["camera_id"], track["local_track_id"])] = cust["customer_id"]
            else:
                # Create a new global customer profile
                c_id = f"cust_{cust_counter}"
                cust_counter += 1
                global_customers.append({
                    "customer_id": c_id,
                    "tracks": [track],
                    "signature_hist": track["signature_hist"]
                })
                global_map[(track["camera_id"], track["local_track_id"])] = c_id
                
        return global_map

def run_store_pipeline(store_id: str, video_dir: str, db: Session, frame_skip: int = 15) -> list:
    """
    Find and process all videos for a store, match across cameras, and write events to DB.
    """
    logger.info(f"Starting pipeline processing for {store_id}...")
    
    # 1. Locate video files
    video_extensions = (".mp4", ".avi", ".mov", ".mkv")
    video_paths = []
    
    if os.path.exists(video_dir):
        for f in os.listdir(video_dir):
            if f.lower().endswith(video_extensions):
                video_paths.append(os.path.join(video_dir, f))
                
    if not video_paths:
        logger.warning(f"No videos found under directory: {video_dir}")
        return []
        
    # 2. Check cache (idempotency): see if we already have events for this store in DB for the video date
    from app.database.models import EventModel
    existing_events_count = db.query(EventModel).filter(
        EventModel.store_id == store_id,
        EventModel.timestamp.like("2026-06-01%")
    ).count()
    
    # Check environment override
    force_process = os.getenv("FORCE_PROCESS_VIDEOS", "false").lower() == "true"
    
    if existing_events_count > 0 and not force_process:
        logger.info(f"Found {existing_events_count} video events for {store_id} in database. Skipping video processing (CACHED).")
        # Load from DB to return
        return db.query(EventModel).filter(
            EventModel.store_id == store_id,
            EventModel.timestamp.like("2026-06-01%")
        ).all()
        
    # 3. Process each camera independently to get local tracks
    all_camera_tracks = {}
    full_local_tracks = {} # nested storage: cam_id -> local_tracks
    
    for v_path in video_paths:
        try:
            processor = VideoProcessor(v_path, store_id, frame_skip=frame_skip)
            cam_key = processor.camera_key
            tracks = processor.process()
            all_camera_tracks[cam_key] = tracks
            full_local_tracks[cam_key] = tracks
        except Exception as e:
            logger.error(f"Error processing video {v_path}: {e}", exc_info=True)
            
    # 4. Associate local tracks across cameras to global customer IDs
    matcher = MultiCameraMatcher(sim_threshold=0.6)
    global_id_map = matcher.associate(all_camera_tracks)
    
    # 5. Generate structured events and insert to database
    generated_events = []
    
    # Gather configuration for ROI details
    config = load_store_config(store_id)
    
    # Track-level aggregated events
    # Collect global timelines to generate customer_entered/customer_exited
    global_customer_times = {} # cust_id -> {'start': datetime, 'end': datetime, 'entry_cam': cam, 'exit_cam': cam}
    
    for cam_id, tracks in full_local_tracks.items():
        cam_config = config.get(cam_id, {})
        rois = cam_config.get("rois", {})
        
        for t_id, track in tracks.items():
            cust_id = global_id_map.get((cam_id, t_id))
            
            # Record global timeline
            if cust_id not in global_customer_times:
                global_customer_times[cust_id] = {
                    "start": track["start_time"],
                    "end": track["end_time"],
                    "entry_cam": cam_id,
                    "exit_cam": cam_id
                }
            else:
                g_time = global_customer_times[cust_id]
                if track["start_time"] < g_time["start"]:
                    g_time["start"] = track["start_time"]
                    g_time["entry_cam"] = cam_id
                if track["end_time"] > g_time["end"]:
                    g_time["end"] = track["end_time"]
                    g_time["exit_cam"] = cam_id
            
            # Local ROI-based event generation
            roi_history = track["roi_history"]
            for idx, (roi_id, ts) in enumerate(roi_history):
                roi_info = rois.get(roi_id, {})
                roi_type = roi_info.get("type")
                roi_name = roi_info.get("name")
                
                # Determine event type
                event_type = None
                extra = {"roi_id": roi_id, "roi_name": roi_name}
                
                if roi_type == "SHELF":
                    event_type = "zone_entered"
                elif roi_type == "BILLING_WAIT":
                    event_type = "billing_started" # joined queue
                elif roi_type == "BILLING_DESK":
                    event_type = "billing_serving" # transitioned to counter
                    
                if event_type:
                    # Write event
                    evt_id = f"evt_{uuid.uuid4().hex[:8]}"
                    db_evt = create_event(
                        db=db,
                        event_id=evt_id,
                        store_id=store_id,
                        camera_id=cam_id,
                        customer_id=cust_id,
                        event_type=event_type,
                        timestamp=ts.isoformat() + "Z",
                        extra_data=extra
                    )
                    generated_events.append(db_evt)
                    
                # Handle exits of ROIs
                # If there's a subsequent ROI, or if it's the last ROI, exit timestamp is the start of next ROI or the track end time
                exit_ts = roi_history[idx+1][1] if idx + 1 < len(roi_history) else track["end_time"]
                exit_event_type = None
                
                if roi_type == "SHELF":
                    exit_event_type = "zone_exited"
                elif roi_type == "BILLING_WAIT":
                    # if exiting wait and not entering desk, they either completed or abandoned
                    # we will map to billing completed when they leave desk
                    exit_event_type = "billing_wait_exited"
                elif roi_type == "BILLING_DESK":
                    exit_event_type = "billing_completed"
                    
                if exit_event_type:
                    evt_id = f"evt_{uuid.uuid4().hex[:8]}"
                    db_evt = create_event(
                        db=db,
                        event_id=evt_id,
                        store_id=store_id,
                        camera_id=cam_id,
                        customer_id=cust_id,
                        event_type=exit_event_type,
                        timestamp=exit_ts.isoformat() + "Z",
                        extra_data=extra
                    )
                    generated_events.append(db_evt)
                    
            # Check for queue length and crowding periodically
            # Since this is simulated stream, we can log periodic occupancy/crowd checks
            # e.g., if there are coordinates, log track coordinates for heatmap generation
            # Save raw coordinates trajectory in event extra_data for ease of plotting heatmaps later
            evt_id = f"evt_{uuid.uuid4().hex[:8]}"
            trajectory = [[float(pt[0]), float(pt[1]), pt[2].isoformat() + "Z"] for pt in track["points"]]
            db_evt = create_event(
                db=db,
                event_id=evt_id,
                store_id=store_id,
                camera_id=cam_id,
                customer_id=cust_id,
                event_type="trajectory_logged",
                timestamp=track["start_time"].isoformat() + "Z",
                extra_data={"trajectory": trajectory}
            )
            generated_events.append(db_evt)
            
    # Write global Entry/Exit events for each customer
    for cust_id, times in global_customer_times.items():
        # Entry event
        entry_evt_id = f"evt_{uuid.uuid4().hex[:8]}"
        db_entry = create_event(
            db=db,
            event_id=entry_evt_id,
            store_id=store_id,
            camera_id=times["entry_cam"],
            customer_id=cust_id,
            event_type="customer_entered",
            timestamp=times["start"].isoformat() + "Z"
        )
        generated_events.append(db_entry)
        
        # Exit event
        exit_evt_id = f"evt_{uuid.uuid4().hex[:8]}"
        db_exit = create_event(
            db=db,
            event_id=exit_evt_id,
            store_id=store_id,
            camera_id=times["exit_cam"],
            customer_id=cust_id,
            event_type="customer_exited",
            timestamp=times["end"].isoformat() + "Z"
        )
        generated_events.append(db_exit)

    # 6. Periodic crowd / queue alerts check
    # Iterate through small time intervals to calculate concurrent occupancy
    # We can inspect maximum counts
    # This is also useful to trigger crowding_detected events
    # Let's say if total occupant tracks in a camera at a given timestamp > 4, trigger crowding_detected event
    
    # Save the output to JSONL file as required by event schema
    jsonl_path = os.path.join("outputs", f"{store_id}_events.jsonl")
    os.makedirs("outputs", exist_ok=True)
    
    with open(jsonl_path, "w") as f:
        for evt in generated_events:
            event_dict = {
                "event_id": evt.event_id,
                "store_id": evt.store_id,
                "camera_id": evt.camera_id,
                "customer_id": evt.customer_id,
                "event_type": evt.event_type,
                "timestamp": evt.timestamp
            }
            if evt.extra_data:
                try:
                    event_dict["extra_data"] = json.loads(evt.extra_data)
                except:
                    event_dict["extra_data"] = evt.extra_data
            f.write(json.dumps(event_dict) + "\n")
            
    logger.info(f"Completed pipeline processing for {store_id}. Logged {len(generated_events)} events to database and saved to {jsonl_path}.")
    return generated_events
