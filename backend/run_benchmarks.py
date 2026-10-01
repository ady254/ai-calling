import os
import subprocess
import time
import json
import csv
import statistics

TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxODQzYzY0Mi02ZTZhLTRjYTktYjQ0Zi0wZTlmMTkzYjg2N2QiLCJleHAiOjE3OTAzNjE4Njh9.8ahXLHogw6sXSFeYr9IKmaJ1iYwQoSaXC9sFQ9ehMhk'
os.environ["API_TOKEN"] = TOKEN

concurrencies = [10, 25, 50, 100]
results = {}

for users in concurrencies:
    print(f"Running benchmark with {users} users...")
    
    # Clear timings log
    if os.path.exists("benchmark_timings.log"):
        os.remove("benchmark_timings.log")
        
    cmd = [
        "venv\\Scripts\\python.exe", "-m", "locust",
        "-f", "locustfile.py",
        "--headless",
        "-u", str(users),
        "-r", str(users),
        "-t", "60s",
        "--csv", f"locust_result_{users}"
    ]
    
    subprocess.run(cmd, capture_output=True, text=True)
    
    # Parse locust stats
    stats_file = f"locust_result_{users}_stats.csv"
    with open(stats_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["Name"] == "Aggregated" or row["Name"] == "GET /call/analytics":
                metrics = {
                    "requests": int(row["Request Count"]),
                    "failures": int(row["Failure Count"]),
                    "rps": float(row["Requests/s"]),
                    "median": float(row["50%"]),
                    "p95": float(row["95%"]),
                    "p99": float(row["99%"]),
                    "max": float(row["Max Response Time"])
                }
                break
                
    # Parse server-side timings
    get_user_business_ms = []
    analytics_total_ms = []
    if os.path.exists("benchmark_timings.log"):
        with open("benchmark_timings.log", 'r') as f:
            for line in f:
                if line.startswith("GET_USER_BUSINESS_MS="):
                    try:
                        get_user_business_ms.append(float(line.strip().split("=")[1]))
                    except: pass
                elif line.startswith("ANALYTICS_TOTAL_MS="):
                    try:
                        analytics_total_ms.append(float(line.strip().split("=")[1]))
                    except: pass
    
    server_metrics = {
        "get_user_business_avg": statistics.mean(get_user_business_ms) if get_user_business_ms else 0,
        "analytics_total_avg": statistics.mean(analytics_total_ms) if analytics_total_ms else 0
    }
    
    results[users] = {
        "locust": metrics,
        "server": server_metrics
    }
    
    print(f"Finished {users} users.")

with open("performance_results.json", "w") as f:
    json.dump(results, f, indent=2)

print("Benchmarks complete. Results saved to performance_results.json")
