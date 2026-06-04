import cv2
import json
import os
import argparse
import sys

def draw_polygon_callback(event, x, y, flags, param):
    points = param["points"]
    img_copy = param["img_copy"]
    img = param["img"]
    
    if event == cv2.EVENT_LBUTTONDOWN:
        points.append([x, y])
        print(f"Added point: [{x}, {y}]")
        # Draw on image
        cv2.circle(img_copy, (x, y), 5, (0, 0, 255), -1)
        if len(points) > 1:
            cv2.line(img_copy, tuple(points[-2]), (x, y), (255, 0, 0), 2)
        cv2.imshow("ROI Annotator", img_copy)

def run_annotator(image_path: str, output_path: str = None):
    if not os.path.exists(image_path):
        print(f"Error: Image not found at {image_path}")
        sys.exit(1)
        
    img = cv2.imread(image_path)
    img_copy = img.copy()
    points = []
    
    cv2.namedWindow("ROI Annotator")
    param = {"points": points, "img_copy": img_copy, "img": img}
    cv2.setMouseCallback("ROI Annotator", draw_polygon_callback, param)
    
    print("\n--- ROI Annotator ---")
    print("Instructions:")
    print("1. Left click on the image to add vertices of the polygon in order.")
    print("2. Press 'c' to close the polygon (connects last point to first).")
    print("3. Press 's' to save the polygon and print coordinates.")
    print("4. Press 'r' to reset points.")
    print("5. Press 'q' to quit.")
    
    while True:
        cv2.imshow("ROI Annotator", img_copy)
        key = cv2.waitKey(1) & 0xFF
        
        if key == ord('r'):
            points.clear()
            img_copy = img.copy()
            print("Reset all points.")
            cv2.imshow("ROI Annotator", img_copy)
            
        elif key == ord('c') and len(points) > 2:
            cv2.line(img_copy, tuple(points[-1]), tuple(points[0]), (255, 0, 0), 2)
            cv2.imshow("ROI Annotator", img_copy)
            print("Closed polygon.")
            
        elif key == ord('s'):
            print("\nFinal Coordinates:")
            print(json.dumps(points))
            if output_path:
                # Append or write to file
                with open(output_path, "w") as f:
                    json.dump(points, f, indent=4)
                print(f"Saved coordinates to {output_path}")
            break
            
        elif key == ord('q'):
            print("Quit annotator.")
            break
            
    cv2.destroyAllWindows()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Interactive ROI Annotation tool")
    parser.add_argument("--image", type=str, help="Path to layout image or video frame")
    parser.add_argument("--output", type=str, default="roi_points.json", help="Path to save output JSON")
    
    # head check
    if len(sys.argv) == 1:
        print("Usage: python roi_annotator.py --image <image_path> [--output <json_output_path>]")
        print("Note: Running this script requires graphical interface support.")
        sys.exit(0)
        
    args = parser.parse_args()
    
    # Try importing Tkinter or check if graphical session is available
    try:
        run_annotator(args.image, args.output)
    except Exception as e:
        print(f"Failed to launch annotator: {e}")
        print("Please run this in an environment with window manager / GUI display.")
