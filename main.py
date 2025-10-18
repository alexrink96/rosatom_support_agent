from fastapi import FastAPI
from pydantic import BaseModel
from llama_cpp import Llama
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "model", "mistral-7b-instruct-v0.2.Q4_K_M.gguf")

llm = Llama(model_path=MODEL_PATH, n_threads=8)

app = FastAPI()

class MessageIn(BaseModel):
    prompt: str

@app.post("/classify")
def classify(msg: MessageIn):
    resp = llm(msg.prompt, max_tokens=30, temperature=0.0)
    text = resp.get("choices", [{}])[0].get("text", "").strip()
    return text
