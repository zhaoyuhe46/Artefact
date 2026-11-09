"""OpenRouter 客户端：对话（强制工具调用拿结构化输出）+ embeddings。统一重试、计时与追踪。"""
import json
import os
import re
import time

import requests

from . import config


class LLMError(RuntimeError):
    pass


def _key():
    k = os.getenv("OPENROUTER_API_KEY")
    if not k:
        raise LLMError("缺少 OPENROUTER_API_KEY")
    return k


def _post(path, payload, timeout=120):
    headers = {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json", "X-Title": "XUYU Message Studio"}
    last = None
    for attempt in range(4):
        try:
            r = requests.post(f"{config.OPENROUTER_BASE}{path}", headers=headers, data=json.dumps(payload), timeout=timeout)
        except requests.RequestException as e:
            last = str(e)
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (408, 429, 500, 502, 503, 504):
            last = f"{r.status_code} {r.text[:200]}"
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise LLMError(f"OpenRouter {r.status_code}: {r.text[:300]}")
        data = r.json()
        if "error" in data:
            last = str(data["error"])[:300]
            time.sleep(2 * (attempt + 1))
            continue
        return data
    raise LLMError(f"OpenRouter 多次重试失败：{last}")


def embed(texts):
    """批量 embedding，返回与输入同序的向量列表。"""
    if isinstance(texts, str):
        texts = [texts]
    out = []
    for i in range(0, len(texts), 64):
        data = _post("/embeddings", {"model": config.EMBED_MODEL, "input": texts[i:i + 64]})
        rows = sorted(data["data"], key=lambda d: d["index"])
        out.extend(r["embedding"] for r in rows)
    return out


def _json_from(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class LLM:
    def __init__(self, trace: list):
        _key()
        self.trace = trace

    def tool_call(self, node, system, user, tool_name, schema, temperature, max_tokens, model=None):
        payload = {
            "model": model or config.GEN_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "tools": [{"type": "function", "function": {"name": tool_name, "description": "提交结构化结果", "parameters": schema}}],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning": {"enabled": False},
        }
        t0 = time.time()
        for _ in range(3):
            data = _post("/chat/completions", payload)
            msg = data["choices"][0]["message"]
            out = None
            for tc in msg.get("tool_calls") or []:
                if tc["function"]["name"] == tool_name:
                    try:
                        out = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        pass
            if out is None:
                out = _json_from(msg.get("content"))      # 兜底：模型把 JSON 写在正文里
            if out is not None:
                self.trace.append({"node": node, "model": payload["model"], "seconds": round(time.time() - t0, 2),
                                   "system": system, "user": user, "output": out, "usage": data.get("usage")})
                return out
        raise LLMError(f"{node}: 三次都没有拿到结构化结果")

    def json_call(self, node, system, user, schema, max_tokens, model, effort="low"):
        """给强制开启推理的模型用（如 Claude）：不用工具，要求只输出符合 schema 的 JSON，由代码解析。"""
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": f"{user}\n\n只输出一个 JSON 对象，不要输出其他文字。JSON Schema：\n{json.dumps(schema, ensure_ascii=False)}"}],
            "max_tokens": max_tokens,
            "reasoning": {"effort": effort},
        }
        t0 = time.time()
        last = ""
        for _ in range(3):
            data = _post("/chat/completions", payload, timeout=180)
            last = data["choices"][0]["message"].get("content") or ""
            out = _json_from(last)
            if out is not None:
                self.trace.append({"node": node, "model": model, "seconds": round(time.time() - t0, 2), "output": out, "usage": data.get("usage")})
                return out
        raise LLMError(f"{node}: 三次都没有解析出 JSON。最后一次输出开头：{last[:200]}")
