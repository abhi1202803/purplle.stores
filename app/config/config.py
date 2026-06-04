import os
import json
import numpy as np

# Root path for configurations
CONFIG_DIR = os.getenv("CONFIG_DIR", "config")

def load_store_config(store_id: str) -> dict:
    """Load the ROI and Homography configurations for a store."""
    filename = f"{store_id}_rois.json"
    filepath = os.path.join(CONFIG_DIR, filename)
    
    # Fallback to absolute paths or default directory if needed
    if not os.path.exists(filepath):
        filepath = os.path.join("d:/Desktop/purple_round2/config", filename)
        
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Configuration file not found: {filepath}")
        
    with open(filepath, "r") as f:
        return json.load(f)

def project_point(homography: list, x: float, y: float) -> tuple:
    """Project a point (x, y) from camera coordinate space to floor plan coordinate space using H."""
    H = np.array(homography, dtype=np.float32)
    # homogeneous coordinates vector
    v = np.array([x, y, 1.0], dtype=np.float32)
    projected = np.dot(H, v)
    w = projected[2]
    if abs(w) > 1e-6:
        x_proj = projected[0] / w
        y_proj = projected[1] / w
        return float(x_proj), float(y_proj)
    return float(projected[0]), float(projected[1])

def get_camera_id_from_filename(filename: str) -> str:
    """Map the video filename to camera config ID keys."""
    base = os.path.basename(filename).lower()
    
    # Store 1 video filenames
    if "cam 1" in base:
        return "CAM 1 - zone"
    elif "cam 2" in base:
        return "CAM 2 - zone"
    elif "cam 3" in base:
        return "CAM 3 - entry"
    elif "cam 5" in base:
        return "CAM 5 - billing"
        
    # Store 2 video filenames
    elif "entry 1" in base or "entry_1" in base:
        return "entry 1"
    elif "entry 2" in base or "entry_2" in base:
        return "entry 2"
    elif "billing" in base:
        return "billing_area"
    elif "zone" in base:
        return "zone"
        
    return os.path.splitext(os.path.basename(filename))[0]
