"""
AI员工政策顾问 - Showtime Demo
后端：FastAPI + SQLite + ChromaDB RAG + DeepSeek
"""
import sqlite3, json, uuid, httpx
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import chromadb

# ─── 配置 ────────────────────────────────────────────────────
BASE_DIR    = "/opt/showtime"
DB_PATH     = f"{BASE_DIR}/api/showtime.db"
CHROMA_DIR  = f"{BASE_DIR}/api/chroma_db"
POLICY_FILE = f"{BASE_DIR}/api/policies.json"
DS_API_KEY  = "sk-7a360aee1efe48148a982850d733cfe2"
DS_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
DS_MODEL    = "deepseek-v4-pro"

# ─── 加载知识库 ───────────────────────────────────────────────
with open(POLICY_FILE, "r", encoding="utf-8") as f:
    POLICY_ITEMS = json.load(f)

# ─── ChromaDB ────────────────────────────────────────────────
chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
policy_collection = chroma_client.get_or_create_collection(
    name="showtime_policies",
    metadata={"hnsw:space": "cosine"}
)

# ─── SQLite ──────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources TEXT DEFAULT '[]',
            retrieved_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
        );
        CREATE TABLE IF NOT EXISTS query_logs (
            id TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            sources TEXT DEFAULT '[]',
            found_in_kb INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    print("✅ SQLite 初始化完成")

    # 同步知识库到向量库
    existing = policy_collection.count()
    if existing < len(POLICY_ITEMS):
        print(f"正在同步 {len(POLICY_ITEMS)} 条政策到向量库...")
        if existing > 0:
            all_ids = policy_collection.get()["ids"]
            if all_ids:
                policy_collection.delete(ids=all_ids)
        policy_collection.add(
            ids=[p["id"] for p in POLICY_ITEMS],
            documents=[f"{p['title']}\n{p['content']}" for p in POLICY_ITEMS],
            metadatas=[{"title": p["title"], "category": p["category"], "source": p["source"]} for p in POLICY_ITEMS]
        )
        print(f"✅ 已同步 {len(POLICY_ITEMS)} 条政策到向量库")
    else:
        print(f"✅ 向量库已有 {existing} 条政策")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="AI员工政策顾问 API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ─── 数据模型 ────────────────────────────────────────────────
class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


# ─── 核心：RAG问答 ───────────────────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    now = datetime.now().isoformat()

    # 创建/确认 session
    conn = get_db()
    exists = conn.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
    if not exists:
        conn.execute("INSERT INTO chat_sessions VALUES (?,?)", (session_id, now))
        conn.commit()

    # 保存用户消息
    conn.execute("INSERT INTO chat_messages VALUES (?,?,?,?,?,?,?)",
                 (str(uuid.uuid4()), session_id, "user", req.question, "[]", 0, now))
    conn.commit()

    # 向量检索
    try:
        results = policy_collection.query(
            query_texts=[req.question],
            n_results=min(5, policy_collection.count()),
            include=["documents", "metadatas", "distances"]
        )
        docs      = results["documents"][0] if results["documents"] else []
        metas     = results["metadatas"][0] if results["metadatas"] else []
        distances = results["distances"][0]  if results["distances"]  else []
        relevant  = [(d, m) for d, m, dist in zip(docs, metas, distances) if dist < 1.2]
    except Exception:
        relevant = []

    # 构建 Prompt
    found_in_kb = len(relevant) > 0
    if found_in_kb:
        context_parts = []
        for doc, meta in relevant:
            context_parts.append(f"【{meta['title']}】（来源：{meta['source']}）\n{doc}")
        context = "\n\n---\n\n".join(context_parts)
        system_prompt = f"""你是一位专业的企业HR政策顾问AI，名为"政策小助手"。你只能基于以下公司制度文件回答员工问题。

严格规则：
1. 只能依据下方检索到的制度内容回答，禁止自由发挥或编造
2. 每个回答末尾必须标注政策依据，格式：【政策依据：XXX】
3. 回答简洁清晰，使用中文，语气专业友好
4. 如果涉及数字（天数、比例、金额），必须精确引用原文

检索到的相关制度内容：
{context}"""
    else:
        system_prompt = """你是一位专业的企业HR政策顾问AI，名为"政策小助手"。
该问题未能在公司制度知识库中检索到相关内容。
你必须严格回复：该问题暂未收录在制度手册中，请联系HR进一步确认。
不得自行编造任何政策内容。"""

    # 调用 DeepSeek
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(DS_BASE_URL,
                headers={"Authorization": f"Bearer {DS_API_KEY}", "Content-Type": "application/json"},
                json={"model": DS_MODEL, "max_tokens": 800,
                      "messages": [{"role": "system", "content": system_prompt},
                                   {"role": "user",   "content": req.question}]})
        if resp.status_code != 200:
            raise Exception(f"DeepSeek API 错误: {resp.status_code}")
        answer = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        answer = f"AI服务暂时不可用，请稍后重试。（{str(e)[:50]}）"

    # 保存AI回复 & 查询日志
    sources = [m["source"] for _, m in relevant]
    conn.execute("INSERT INTO chat_messages VALUES (?,?,?,?,?,?,?)",
                 (str(uuid.uuid4()), session_id, "assistant", answer,
                  json.dumps(sources, ensure_ascii=False), len(relevant), now))
    conn.execute("INSERT INTO query_logs VALUES (?,?,?,?,?,?)",
                 (str(uuid.uuid4()), req.question, answer,
                  json.dumps(sources, ensure_ascii=False), 1 if found_in_kb else 0, now))
    conn.commit()
    conn.close()

    return {
        "answer": answer,
        "session_id": session_id,
        "sources": sources,
        "retrieved_count": len(relevant),
        "found_in_kb": found_in_kb
    }


@app.get("/api/health")
def health():
    return {"ok": True, "kb_count": policy_collection.count(), "model": DS_MODEL}


@app.get("/api/stats")
def stats():
    conn = get_db()
    total    = conn.execute("SELECT COUNT(*) FROM query_logs").fetchone()[0]
    in_kb    = conn.execute("SELECT COUNT(*) FROM query_logs WHERE found_in_kb=1").fetchone()[0]
    sessions = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    conn.close()
    return {"total_queries": total, "found_in_kb": in_kb, "sessions": sessions, "kb_policies": len(POLICY_ITEMS)}


# ─── 静态前端 ────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=f"{BASE_DIR}/frontend", html=True), name="static")
