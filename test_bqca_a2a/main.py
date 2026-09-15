import inspect
import json
import logging
import os
from typing import Any, Dict, Optional
import uvicorn
import vertexai
from fastapi import FastAPI, encoders, responses, Request
from vertexai import agent_engines
from google.adk.sessions import VertexAiSessionService, InMemorySessionService

try:
    from restaurant_agent.agent import root_agent
except ModuleNotFoundError:
    from agent import root_agent

app = FastAPI()

config_path = "restaurant_agent/config.json" if os.path.exists("restaurant_agent/config.json") else "config.json"
with open(config_path) as f:
    config_json = json.load(f)
PROJECT_ID = config_json["PROJECT_ID"]
LOCATION = config_json["LOCATION"]
MODEL_REGION = config_json["MODEL_REGION"]

def session_service_builder():
    engine_id = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID") or os.environ.get("REASONING_ENGINE_ID")
    if engine_id:
        return VertexAiSessionService(
            project=PROJECT_ID,
            location=LOCATION,
            agent_engine_id=engine_id,
        )
    return InMemorySessionService()

vertexai.init(project=PROJECT_ID, location=MODEL_REGION)
adk_app = agent_engines.AdkApp(
    agent=root_agent,
    session_service_builder=session_service_builder,
)
adk_app.set_up()

@app.get("/")
async def root_health():
    return {"status": "ok"}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

def _encode_chunk_to_json(chunk):
  try:
    json_chunk = encoders.jsonable_encoder(chunk)
    return json.dumps(json_chunk) + "\n"
  except Exception:
    logging.exception("Failed to encode chunk")
    return None

async def json_generator(output):
    if hasattr(output, "__aiter__"):
        async for chunk in output:
            encoded_chunk = _encode_chunk_to_json(chunk)
            if encoded_chunk is not None:
                yield encoded_chunk
    else:
        for chunk in output:
            encoded_chunk = _encode_chunk_to_json(chunk)
            if encoded_chunk is not None:
                yield encoded_chunk

async def _invoke_callable_or_raise(invocation_callable, invocation_payload):
  if inspect.iscoroutinefunction(invocation_callable):
    return await invocation_callable(**invocation_payload)
  else:
    return invocation_callable(**invocation_payload)

@app.post("/api/reasoning_engine")
async def query(request: Request) -> responses.JSONResponse:
    request_json = await request.json()
    class_method = request_json.get("class_method")
    input_val = request_json.get("input")
    if class_method == "agent_run":
        output = list(adk_app.stream_query(**(input_val or {})))
    else:
        method = getattr(adk_app, class_method)
        output = await _invoke_callable_or_raise(method, input_val or {})
    try:
      json_serialized_content = encoders.jsonable_encoder({"output": output})
    except ValueError as encoding_error:
      logging.exception("Failed to encode response")
      raise encoding_error
    return responses.JSONResponse(content=json_serialized_content)

@app.post("/api/stream_reasoning_engine")
async def stream_query(request: Request) -> responses.StreamingResponse:
    request_json = await request.json()
    class_method = request_json.get("class_method")
    input_val = request_json.get("input")
    method = getattr(adk_app, class_method)
    output = await _invoke_callable_or_raise(method, input_val or {})
    return responses.StreamingResponse(
        content=json_generator(output),
        media_type="application/json",
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))