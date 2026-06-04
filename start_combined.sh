#!/bin/bash
# Start FastAPI backend in background on port 8000
echo "Starting FastAPI backend on port 8000..."
python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8000 &

# Wait for API to initialize
sleep 3

# Start Streamlit frontend in foreground on the port specified by the environment (default: 8501)
PORT=${PORT:-8501}
echo "Starting Streamlit dashboard on port $PORT..."
python -m streamlit run app/dashboard/main.py --server.port $PORT --server.address 0.0.0.0
