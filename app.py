# app.py
# 最小可用 Tarot MCP-style 服务 (Python + Flask)
# 提供 /draw_one 和 /draw_three 两个 endpoint
# 可选使用 Redis 保存 session 去重（生产推荐）
# 若不配置 REDIS_URL 则使用内存保存（容器重启会丢失）

import os
import csv
import secrets
import threading
import time
from typing import List, Dict, Any
from flask import Flask, request, jsonify, abort
# ---- Verdure / MCP 探测兼容补丁开始 ----
from flask import current_app, make_response
import json

# 可配置项（环境变量）
PORT = int(os.getenv("PORT", "8080"))
API_KEY = os.getenv("API_KEY", "")  # 若设置，要求请求 header 'X-API-KEY' 匹配
REDIS_URL = os.getenv("REDIS_URL", "")  # 可选；若设置则使用 Redis 持久化 session

# 尝试导入 redis（如果 REDIS_URL 未配置则不用）
redis_client = None
if REDIS_URL:
    try:
        import redis
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:
        print("警告：尝试连接 Redis 失败，继续使用内存（若需要 Redis 请检查 REDIS_URL 和 redis 库）", e)
        redis_client = None

app = Flask(__name__)

# 线程锁：保护内存 session 存储
lock = threading.Lock()

# 内存 session 存储（仅在无 Redis 时使用）
# 格式: { session_id: set([used_index, ...]) }
memory_sessions: Dict[str, set] = {}

# 加载 Tarot 数据（从 data/tarot.csv 或 data/tarot_sample.csv）
TAROT_CSV_PATHS = [
    "data/tarot.csv",         # 若你上传了完整 CSV，请放在 data/tarot.csv
    "data/tarot_sample.csv"   # 模板自带的示例
]

tarot_cards: Dict[int, Dict[str, str]] = {}

def load_tarot():
    global tarot_cards
    for p in TAROT_CSV_PATHS:
        if os.path.exists(p):
            with open(p, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 期望 CSV 包含列：Index,Card,Chinese Name,Japanese Name,Upright Meaning,Reversed Meaning
                    try:
                        idx = int(row.get("Index", row.get("Index ", "")).strip())
                    except:
                        # 如果 Index 不可解析，跳过
                        continue
                    tarot_cards[idx] = {
                        "Card": row.get("Card", "").strip(),
                        "ChineseName": row.get("Chinese Name", row.get("ChineseName", "")).strip(),
                        "JapaneseName": row.get("Japanese Name", row.get("JapaneseName", "")).strip(),
                        "Upright": row.get("Upright Meaning", "").strip(),
                        "Reversed": row.get("Reversed Meaning", "").strip(),
                    }
            print(f"Loaded tarot data from {p}, total cards: {len(tarot_cards)}")
            return
    print("未找到 tarot csv，服务启动但无卡片数据。请在 data/tarot.csv 放入完整 CSV 后重启。")

load_tarot()

# 辅助：检查 API key（若设置）
def require_api_key():
    if API_KEY:
        key = request.headers.get("X-API-KEY", "")
        if not key or key != API_KEY:
            abort(jsonify({"code":401, "error":"Invalid API key"}), 401)

# session helpers：使用 Redis（若可用）或内存
def get_used_set(session_id: str) -> set:
    if redis_client:
        members = redis_client.smembers(f"tarot:used:{session_id}") or []
        return set(int(x) for x in members)
    else:
        with lock:
            return set(memory_sessions.get(session_id, set()))

def add_used(session_id: str, index: int):
    if redis_client:
        redis_client.sadd(f"tarot:used:{session_id}", index)
    else:
        with lock:
            s = memory_sessions.setdefault(session_id, set())
            s.add(index)

def clear_used(session_id: str):
    if redis_client:
        redis_client.delete(f"tarot:used:{session_id}")
    else:
        with lock:
            memory_sessions.pop(session_id, None)

# 随机抽 n 张（确保不重复；若剩余不足，按策略处理）
def draw_cards(session_id: str, n: int, reset_if_exhausted: bool = True) -> List[Dict[str, Any]]:
    if not tarot_cards:
        raise RuntimeError("No tarot card data loaded.")
    total = sorted(tarot_cards.keys())
    used = get_used_set(session_id)
    remaining = [i for i in total if i not in used]
    # 若可用卡少于 n：根据 reset_if_exhausted 决定重置或报错
    if len(remaining) < n:
        if reset_if_exhausted:
            clear_used(session_id)
            used = set()
            remaining = [i for i in total]
        else:
            raise RuntimeError("Not enough distinct cards remaining for session.")

    # 安全随机选择 n 个不重复 index
    picks = []
    for _ in range(n):
        idx = secrets.choice(remaining)
        picks.append(idx)
        remaining.remove(idx)

    # 记录已用
    for idx in picks:
        add_used(session_id, idx)

    # 构建返回结构（随机决定正逆位）
    out = []
    for idx in picks:
        card = tarot_cards.get(idx, {})
        orientation = "upright" if secrets.randbelow(2) == 0 else "reversed"
        meaning = card.get("Upright") if orientation == "upright" else card.get("Reversed")
        out.append({
            "index": idx,
            "card": card.get("Card"),
            "chinese_name": card.get("ChineseName"),
            "japanese_name": card.get("JapaneseName"),
            "orientation": orientation,
            "meaning": meaning
        })
    return out


# helper: 构造 MCP 风格工具定义（非常简化，但满足 discovery）
def build_tools_manifest():
    # 每个 tool 的 input 字段可用 JSON Schema 精确描述；这里给最小示例
    return [
        {
            "name": "draw_one",
            "description": "Draw one tarot card. POST JSON {session_id: string}",
            "input": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "reset_if_exhausted": {"type": "boolean"}
                },
                "required": ["session_id"]
            }
        },
        {
            "name": "draw_three",
            "description": "Draw three tarot cards (past/present/future). POST JSON {session_id: string}",
            "input": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "reset_if_exhausted": {"type": "boolean"}
                },
                "required": ["session_id"]
            }
        }
    ]

# JSON-RPC 响应构造器
def make_jsonrpc_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}

def make_jsonrpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# 简单的 CORS 支持（如你已使用 flask_cors 可跳过此段）
@app.after_request
def add_cors_headers(response):
    # 允许 Verdure 平台访问（根据需要收紧 origin）
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-KEY, Authorization'
    return response

# ---- Verdure / MCP 探测兼容补丁开始 (修正版) ----
@app.route("/", methods=["GET", "POST", "OPTIONS"])
def handle_mcp_request():
    # 1. 处理 CORS 预检
    if request.method == "OPTIONS":
        return make_response("", 200)

    # 2. 获取 JSON 数据
    req_json = request.get_json(silent=True)
    current_app.logger.info(f"MCP Request: {req_json}")

    # 3. 如果不是 JSON-RPC (比如浏览器直接访问)，返回基础信息
    if not req_json or not isinstance(req_json, dict) or "method" not in req_json:
        return jsonify({
            "status": "online",
            "message": "Tarot MCP Server is running. Please use an MCP client to connect.",
            "endpoints": ["/draw_one", "/draw_three"]
        }), 200

    # 4. 处理标准 MCP JSON-RPC 请求
    method = req_json.get("method")
    req_id = req_json.get("id")
    params = req_json.get("params", {})

    # --- A. 握手 (Initialize) ---
    if method == "initialize":
        return jsonify({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "tarot-mcp",
                    "version": "1.0.0"
                }
            }
        })

    # --- B. 握手确认 (Initialized) ---
    elif method == "notifications/initialized":
        # 客户端确认握手完成，不需要回复内容，但要返回 200
        return "", 200

    # --- C. 列出工具 (List Tools) ---
    elif method == "tools/list":
        return jsonify({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": build_tools_manifest()
            }
        })

    # --- D. 调用工具 (Call Tool) ---
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        session_id = tool_args.get("session_id", "default_session")
        reset_if = tool_args.get("reset_if_exhausted", True)

        try:
            result_text = ""
            # 根据工具名调用内部逻辑
            if tool_name == "draw_one":
                cards = draw_cards(session_id, 1, reset_if_exhausted=reset_if)
                # 将结果转为 AI 易读的文本
                c = cards[0]
                result_text = f"抽到的牌是：{c['chinese_name']} ({c['card']})\n正逆位：{'正位' if c['orientation']=='upright' else '逆位'}\n含义：{c['meaning']}"
            
            elif tool_name == "draw_three":
                cards = draw_cards(session_id, 3, reset_if_exhausted=reset_if)
                roles = ["过去", "现在", "未来"]
                lines = []
                for i, c in enumerate(cards):
                    role = roles[i] if i < 3 else f"位置{i+1}"
                    lines.append(f"【{role}】：{c['chinese_name']} ({c['card']}) - {'正位' if c['orientation']=='upright' else '逆位'}\n含义：{c['meaning']}")
                result_text = "\n\n".join(lines)
            
            else:
                return jsonify(make_jsonrpc_error(req_id, -32601, f"Tool {tool_name} not found"))

            # 返回 MCP 标准格式结果
            return jsonify({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": result_text,
                            # 也可以把原始 JSON 附上
                            "metadata": {"raw_cards": cards} 
                        }
                    ]
                }
            })

        except Exception as e:
            return jsonify(make_jsonrpc_error(req_id, -32000, str(e)))

    # --- E. 未知方法 ---
    else:
        # 对于 Ping 等其他方法，返回空结果以防报错
        return jsonify({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {}
        })
# ---- Verdure / MCP 探测兼容补丁结束 ----

# 健康检查
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"code":0, "status":"ok", "time": int(time.time())})

# 单张抽牌
@app.route("/draw_one", methods=["POST"])
def draw_one():
    require_api_key()
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id") or payload.get("session") or request.args.get("session_id")
    if not session_id:
        return jsonify({"code":400, "error":"Missing session_id"}), 400
    reset_if_exhausted = payload.get("reset_if_exhausted", True)
    try:
        cards = draw_cards(session_id, 1, reset_if_exhausted=bool(reset_if_exhausted))
    except Exception as e:
        return jsonify({"code":500, "error": str(e)}), 500
    return jsonify({"code":0, "session_id": session_id, "cards": cards})

# 三张抽牌（过去/现在/未来）
@app.route("/draw_three", methods=["POST"])
def draw_three():
    require_api_key()
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id") or payload.get("session") or request.args.get("session_id")
    if not session_id:
        return jsonify({"code":400, "error":"Missing session_id"}), 400
    reset_if_exhausted = payload.get("reset_if_exhausted", True)
    try:
        cards = draw_cards(session_id, 3, reset_if_exhausted=bool(reset_if_exhausted))
    except Exception as e:
        return jsonify({"code":500, "error": str(e)}), 500
    # 标记牌位
    roles = ["past", "present", "future"]
    for i, c in enumerate(cards):
        c["role"] = roles[i] if i < len(roles) else f"pos{i}"
    return jsonify({"code":0, "session_id": session_id, "cards": cards})

# 可选：重置一个 session（用于测试）
@app.route("/reset_session", methods=["POST"])
def reset_session():
    require_api_key()
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id")
    if not session_id:
        return jsonify({"code":400, "error":"Missing session_id"}), 400
    clear_used(session_id)
    return jsonify({"code":0, "session_id": session_id, "message":"cleared"})

if __name__ == "__main__":
    # 本地调试启动
    app.run(host="0.0.0.0", port=PORT)
