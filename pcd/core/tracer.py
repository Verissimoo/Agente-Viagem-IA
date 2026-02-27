import json
import time
import traceback
from datetime import datetime
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

class PipelineTracer:
    def __init__(self, request_id: str):
        self.request_id = request_id
        self.events: List[Dict[str, Any]] = []

    def log_event(
        self, 
        stage: str, 
        status: str, 
        message: str = "", 
        latency_ms: float = 0.0, 
        offers_count: Optional[int] = None, 
        error: Optional[str] = None
    ):
        event = {
            "request_id": self.request_id,
            "stage": stage,
            "status": status,
            "ts": datetime.now().isoformat(),
            "latency_ms": round(latency_ms, 2),
            "offers_count": offers_count,
            "message": message,
            "error": error
        }
        self.events.append(event)

    @contextmanager
    def track_stage(self, stage: str, message: str = ""):
        start_time = time.time()
        self.log_event(stage=stage, status="start", message=message)
        try:
            # Yield to execution
            result_info = {"offers_count": None}
            yield result_info
            
            latency = (time.time() - start_time) * 1000
            self.log_event(
                stage=stage, 
                status="end", 
                message=message, 
                latency_ms=latency, 
                offers_count=result_info.get("offers_count")
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            self.log_event(
                stage=stage, 
                status="error", 
                message=str(e), 
                latency_ms=latency, 
                error=traceback.format_exc()
            )
            raise e

    def save(self, output_path: str):
        if not output_path:
            return
            
        with open(output_path, "w", encoding="utf-8") as f:
            if output_path.endswith(".jsonl"):
                for event in self.events:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
            else:
                json.dump(self.events, f, indent=2, ensure_ascii=False)
