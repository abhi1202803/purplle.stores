# Live Cloud Deployment Guide

This document describes how to deploy the Store Intelligence System (FastAPI + Streamlit + SQLite) live to the cloud for free using a single-container setup.

---

## 🚀 Option 1: Hugging Face Spaces (Recommended – 100% Free & Persistent)

Hugging Face Spaces is the ideal platform because it provides free container hosting, automatically installs machine learning and GPU/CPU visual dependencies (OpenCV/PyTorch), and does not put the container to sleep (persistent running).

### Step-by-Step Setup:
1. **Create Space**:
   - Go to [Hugging Face Spaces](https://huggingface.co/spaces) and click **Create new Space**.
   - **Space Name**: `purplle-store-intelligence` (or your choice).
   - **SDK**: Select **Docker**.
   - **Docker Template**: Select **Blank** (do not select Streamlit, as we need custom dependencies from the `Dockerfile`).
   - **Space License**: `Apache 2.0` (or choice).
   - **Visibility**: Public (so you can share the link).

2. **Add Remote & Push**:
   Open a terminal in your project directory and run:
   ```bash
   # Add the Hugging Face Space repository as a remote
   git remote add hf https://huggingface.co/spaces/<your-username>/<your-space-name>

   # Force push the main branch to Hugging Face
   git push -f hf main
   ```
   *Note: If prompted for credentials, use your Hugging Face username and your Hugging Face Access Token (retrieve/create one at Settings -> Access Tokens with Write permission).*

3. **Monitor Build**:
   - Hugging Face will automatically read the `Dockerfile`, build the container, download the YOLO weights, and launch the backend and frontend.
   - Once the build succeeds, your **Streamlit Command Center** will be live at:
     `https://huggingface.co/spaces/<your-username>/<your-space-name>`

---

## 🌐 Option 2: Render (Free Web Service)

Render builds and runs Docker applications directly from your GitHub repository.

### Step-by-Step Setup:
1. Log in to [Render](https://render.com/) and click **New** -> **Web Service**.
2. Connect your GitHub repository: `https://github.com/abhi1202803/purplle.stores`.
3. Configure the service:
   - **Name**: `purplle-store-intel`
   - **Region**: Select the closest region.
   - **Branch**: `main`
   - **Runtime**: **Docker** (Render will automatically detect the root `Dockerfile`).
4. Click **Deploy Web Service**.
5. Render will build the Docker container and host it. Once deployed, you will get a live URL (e.g. `https://purplle-store-intel.onrender.com`).

---

## ⚡ Option 3: Railway (Fastest Setup)

Railway provides $5 of free credit which runs containers continuously for weeks.

### Step-by-Step Setup:
1. Log in to [Railway](https://railway.app/).
2. Click **New Project** -> **Deploy from GitHub repo**.
3. Select your repository `purplle.stores`.
4. Click **Deploy Now**.
5. Railway will detect the `Dockerfile` and start building.
6. Once deployed, go to the Service Settings on Railway and click **Generate Domain** to get your public live link!

---

## ⚙️ How the Single-Container Architecture Works
To make cloud deployment seamless, we configured a startup wrapper script [start_combined.sh](file:///d:/Desktop/purple_round2/start_combined.sh):
1. The container starts.
2. It launches the **FastAPI REST API** in the background on port `8000`.
3. It initializes the SQLite database, imports the simulated retail transactions, and runs initial operations checks.
4. It launches the **Streamlit Dashboard** in the foreground on the port required by the hosting provider (`$PORT` or default `8501`).
5. Streamlit queries the FastAPI backend locally at `http://localhost:8000`, bypassing complex external networking.
