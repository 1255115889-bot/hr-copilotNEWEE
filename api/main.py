"""
HR Copilot - Python FastAPI 后端
数据层: SQLite (业务数据) + ChromaDB (知识库向量检索)
"""
import sqlite3, json, uuid, os, httpx
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import chromadb

# ─── 路径配置 ───────────────────────────────────────────────
BASE_DIR   = "/opt/hr-copilot"
DB_PATH    = f"{BASE_DIR}/api/hr_copilot.db"
CHROMA_DIR = f"{BASE_DIR}/api/chroma_db"

# ─── DeepSeek 配置 ───────────────────────────────────────────
DS_API_KEY  = "sk-7a360aee1efe48148a982850d733cfe2"
DS_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
DS_MODEL    = "deepseek-v4-pro"

# ─── ChromaDB 初始化 ─────────────────────────────────────────
chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
kb_collection = chroma_client.get_or_create_collection(
    name="hr_knowledge_base",
    metadata={"hnsw:space": "cosine"}
)

# ─── SQLite 初始化 ───────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS kb_items (
            id       TEXT PRIMARY KEY,
            title    TEXT NOT NULL,
            category TEXT NOT NULL,
            content  TEXT NOT NULL,
            author   TEXT DEFAULT 'HR部门',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS applications (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            applicant   TEXT NOT NULL,
            dept        TEXT NOT NULL,
            status      TEXT DEFAULT 'pending',
            detail      TEXT DEFAULT '{}',
            submit_at   TEXT NOT NULL,
            approved_at TEXT,
            reject_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS chat_logs (
            id         TEXT PRIMARY KEY,
            session_id TEXT,
            role       TEXT,
            content    TEXT,
            created_at TEXT
        );
    """)
    conn.commit()

    # 插入示例知识库数据（仅首次）
    existing = c.execute("SELECT COUNT(*) FROM kb_items").fetchone()[0]
    if existing == 0:
        sample_kb = [
            ("年假政策", "假期", "员工入职满1年可享有5天带薪年假，满3年享有10天，满5年享有15天，最高上限为15天。年假需提前3个工作日申请，经直属上级审批后方可休假。年假当年有效，不可跨年累计。"),
            ("病假政策", "假期", "员工因病需休假，须提供医院诊断证明。病假前3天工资全额发放，3天以上按80%发放。连续病假超过30天，需办理病假手续并提交相关证明材料。"),
            ("产假政策", "假期", "女员工顺产产假98天，剖腹产增加15天，生育多胞胎每增加一个婴儿增加15天。男员工陪产假15天。产假期间工资按正常标准发放。"),
            ("薪资构成与发放", "薪资", "薪资由基本工资、绩效奖金、津贴三部分构成。每月15日发放上月工资，如遇节假日提前发放。绩效奖金按季度考核结果发放，考核优秀、良好、合格分别对应120%、100%、80%的绩效系数。"),
            ("在职证明申请流程", "证明", "在职证明申请通过HR系统提交，填写用途说明。HR在2个工作日内出具，加盖公司公章。证明内容包含：姓名、职位、部门、入职时间、公司信息。紧急情况可联系HR直接处理。"),
            ("收入证明申请流程", "证明", "收入证明申请需提前5个工作日申请。需提供申请用途（如贷款、签证等）。内容包含月均税前收入、社保缴纳情况。收入证明有效期3个月，超期需重新申请。"),
            ("报销政策与流程", "报销", "费用报销须在发生后30天内提交，超期不予报销。差旅费需提前申请，机票购买须符合公司差旅标准（经济舱）。报销凭证须为正规发票，金额超过5000元需部门总监审批。"),
            ("考勤制度", "考勤", "工作时间：周一至周五 9:00-18:00，午休12:00-13:00。弹性打卡时间：9:00-9:30，超过9:30视为迟到。每月允许3次迟到/早退，超过3次扣除相应绩效分。远程办公需提前申请并获批。"),
            ("合同续签流程", "合同", "劳动合同到期前60天，HR将通知员工续签。续签意向须在到期前30天确认。第一次固定期限合同为1年，第二次为3年，第三次起签订无固定期限合同。"),
            ("婚假政策", "假期", "员工结婚可享有婚假3天，双方均为晚婚（男25周岁、女23周岁以上）可享有额外10天晚婚假。婚假须在领取结婚证后1年内使用，需提前7天申请并提供结婚证复印件。"),
        ]
        for title, category, content in sample_kb:
            item_id = str(uuid.uuid4())
            now = datetime.now().strftime("%Y-%m-%d")
            c.execute("INSERT INTO kb_items VALUES (?,?,?,?,?,?)",
                      (item_id, title, category, content, "HR部门", now))
            # 同步写入向量库
            kb_collection.add(
                ids=[item_id],
                documents=[f"{title}\n{content}"],
                metadatas=[{"title": title, "category": category}]
            )
        conn.commit()
        print(f"✅ 初始化了 {len(sample_kb)} 条知识库数据")

    # 插入示例申请数据
    existing_apps = c.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    if existing_apps == 0:
        sample_apps = [
            ("APP001", "在职证明申请", "张明", "技术部", "pending",  json.dumps({"purpose":"银行贷款","urgency":"普通"}, ensure_ascii=False), "2025-06-10 10:30"),
            ("APP002", "年假申请",     "李小花","市场部", "approved", json.dumps({"leaveType":"年假","startDate":"2025-06-20","endDate":"2025-06-22"}, ensure_ascii=False), "2025-06-08 14:20"),
            ("APP003", "收入证明申请", "王大伟","销售部", "pending",  json.dumps({"purpose":"购房贷款","urgency":"紧急"}, ensure_ascii=False), "2025-06-11 09:00"),
            ("APP004", "薪资异常申诉", "陈小静","运营部", "processing",json.dumps({"month":"2025年5月","issue":"加班费计算有误"}, ensure_ascii=False), "2025-06-09 16:00"),
        ]
        for row in sample_apps:
            c.execute("INSERT INTO applications(id,type,applicant,dept,status,detail,submit_at) VALUES (?,?,?,?,?,?,?)", row)
        conn.commit()

    conn.close()
    print("✅ SQLite 数据库初始化完成")


# ─── Lifespan ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="HR Copilot API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ════════════════════════════════════════════════════════════
# 知识库 API
# ════════════════════════════════════════════════════════════
class KBItem(BaseModel):
    title: str
    category: str
    content: str
    author: Optional[str] = "HR部门"

@app.get("/api/kb")
def list_kb(search: str = "", category: str = ""):
    conn = get_db()
    q = "SELECT * FROM kb_items WHERE 1=1"
    params = []
    if search:
        q += " AND (title LIKE ? OR content LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if category:
        q += " AND category = ?"
        params.append(category)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/kb")
def create_kb(item: KBItem):
    item_id = str(uuid.uuid4())
    now = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    conn.execute("INSERT INTO kb_items VALUES (?,?,?,?,?,?)",
                 (item_id, item.title, item.category, item.content, item.author, now))
    conn.commit()
    conn.close()
    # 同步向量库
    kb_collection.add(
        ids=[item_id],
        documents=[f"{item.title}\n{item.content}"],
        metadatas=[{"title": item.title, "category": item.category}]
    )
    return {"id": item_id, "title": item.title, "category": item.category,
            "content": item.content, "author": item.author, "updated_at": now}

@app.put("/api/kb/{item_id}")
def update_kb(item_id: str, item: KBItem):
    now = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    conn.execute("UPDATE kb_items SET title=?,category=?,content=?,author=?,updated_at=? WHERE id=?",
                 (item.title, item.category, item.content, item.author, now, item_id))
    conn.commit()
    conn.close()
    # 更新向量库
    kb_collection.update(
        ids=[item_id],
        documents=[f"{item.title}\n{item.content}"],
        metadatas=[{"title": item.title, "category": item.category}]
    )
    return {"ok": True}

@app.delete("/api/kb/{item_id}")
def delete_kb(item_id: str):
    conn = get_db()
    conn.execute("DELETE FROM kb_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    try:
        kb_collection.delete(ids=[item_id])
    except:
        pass
    return {"ok": True}


# ════════════════════════════════════════════════════════════
# AI 问答 API（RAG：向量检索 → DeepSeek）
# ════════════════════════════════════════════════════════════
class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None

@app.post("/api/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    # 1. 向量检索：从知识库找最相关的 Top-5 条目
    try:
        results = kb_collection.query(
            query_texts=[req.question],
            n_results=min(5, kb_collection.count()),
            include=["documents", "metadatas", "distances"]
        )
        retrieved_docs = results["documents"][0] if results["documents"] else []
        retrieved_meta = results["metadatas"][0] if results["metadatas"] else []
        # 过滤相似度过低的（距离 > 1.5 认为不相关）
        distances = results["distances"][0] if results["distances"] else []
        relevant = [(doc, meta) for doc, meta, dist in zip(retrieved_docs, retrieved_meta, distances) if dist < 1.5]
    except Exception as e:
        relevant = []

    # 2. 构建 System Prompt
    if relevant:
        context = "\n\n---\n\n".join(
            [f"【{meta['title']}（{meta['category']}）】\n{doc}" for doc, meta in relevant]
        )
        system_prompt = f"""你是企业HR政策顾问AI，名为"HR Copilot"。

严格规则：
1. 只能基于下方检索到的知识库内容回答，禁止自由编造
2. 如果检索内容与问题无关，必须回复："该问题暂未收录在知识库中，请联系 HR 进一步确认"
3. 每个回答末尾注明来源，格式：【来源：《政策名称》】
4. 使用中文回答，清晰简洁

检索到的相关知识库内容（共{len(relevant)}条）：
{context}

业务卡片触发规则（在回答末尾附加JSON，以|||ACTION:{{开头）：
- 请假相关 → {{"type":"action","title":"请假申请","icon":"event_available","desc":"在线提交请假申请，经上级审批后生效","time":"1个工作日","button":"立即申请"}}
- 在职证明 → {{"type":"action","title":"在职证明申请","icon":"badge","desc":"HR在2个工作日内出具证明","time":"2个工作日","button":"立即申请"}}
- 收入证明 → {{"type":"action","title":"收入证明申请","icon":"account_balance_wallet","desc":"需提前5个工作日申请","time":"3个工作日","button":"立即申请"}}
- 薪资查询 → {{"type":"info","title":"薪资查询","icon":"payments","desc":"查看薪资明细与发放记录","button":"立即查看"}}
- 考勤查询 → {{"type":"info","title":"考勤记录","icon":"schedule","desc":"查看打卡与考勤记录","button":"立即查看"}}
- 薪资/考勤异常 → {{"type":"alert","title":"提交申诉","icon":"report_problem","desc":"对薪资或考勤异常提出申诉","time":"3个工作日","button":"提交申诉"}}"""
    else:
        system_prompt = """你是企业HR政策顾问AI。知识库中未检索到相关内容，必须回复："该问题暂未收录在知识库中，请联系 HR 进一步确认"。不得自行编造任何政策内容。"""

    # 3. 记录用户消息
    conn = get_db()
    conn.execute("INSERT INTO chat_logs VALUES (?,?,?,?,?)",
                 (str(uuid.uuid4()), session_id, "user", req.question, datetime.now().isoformat()))

    # 4. 调用 DeepSeek
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(DS_BASE_URL, headers={
            "Authorization": f"Bearer {DS_API_KEY}",
            "Content-Type": "application/json"
        }, json={
            "model": DS_MODEL,
            "max_tokens": 1000,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": req.question}
            ]
        })

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"DeepSeek API 错误: {resp.text}")

    answer = resp.json()["choices"][0]["message"]["content"]

    # 5. 记录 AI 回复
    conn.execute("INSERT INTO chat_logs VALUES (?,?,?,?,?)",
                 (str(uuid.uuid4()), session_id, "assistant", answer, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    # 6. 解析 Action Card
    action_card = None
    import re
    match = re.search(r'\|\|\|ACTION:(\{.*?\})', answer, re.DOTALL)
    if match:
        try:
            action_card = json.loads(match.group(1))
            answer = answer[:match.start()].strip()
        except:
            pass

    return {
        "answer": answer,
        "action_card": action_card,
        "session_id": session_id,
        "sources": [m["title"] for _, m in relevant],
        "retrieved_count": len(relevant)
    }


# ════════════════════════════════════════════════════════════
# 申请 API
# ════════════════════════════════════════════════════════════
class ApplicationReq(BaseModel):
    type: str
    applicant: str
    dept: str
    detail: dict = {}

class RejectReq(BaseModel):
    reason: Optional[str] = "不符合申请条件"

@app.get("/api/applications")
def list_applications(applicant: str = "", status: str = ""):
    conn = get_db()
    q = "SELECT * FROM applications WHERE 1=1"
    params = []
    if applicant:
        q += " AND applicant=?"; params.append(applicant)
    if status:
        q += " AND status=?"; params.append(status)
    q += " ORDER BY submit_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"] or "{}")
        result.append(d)
    return result

@app.post("/api/applications")
def create_application(req: ApplicationReq):
    app_id = "APP" + datetime.now().strftime("%H%M%S")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute("INSERT INTO applications(id,type,applicant,dept,status,detail,submit_at) VALUES (?,?,?,?,?,?,?)",
                 (app_id, req.type, req.applicant, req.dept, "pending", json.dumps(req.detail, ensure_ascii=False), now))
    conn.commit()
    conn.close()
    return {"id": app_id, "status": "pending", "submit_at": now}

@app.put("/api/applications/{app_id}/approve")
def approve_application(app_id: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute("UPDATE applications SET status='approved', approved_at=? WHERE id=?", (now, app_id))
    conn.commit(); conn.close()
    return {"ok": True}

@app.put("/api/applications/{app_id}/reject")
def reject_application(app_id: str, req: RejectReq):
    conn = get_db()
    conn.execute("UPDATE applications SET status='rejected', reject_reason=? WHERE id=?", (req.reason, app_id))
    conn.commit(); conn.close()
    return {"ok": True}


# ════════════════════════════════════════════════════════════
# 统计 API
# ════════════════════════════════════════════════════════════
@app.get("/api/analytics")
def analytics():
    conn = get_db()
    total    = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    pending  = conn.execute("SELECT COUNT(*) FROM applications WHERE status='pending'").fetchone()[0]
    approved = conn.execute("SELECT COUNT(*) FROM applications WHERE status='approved'").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM applications WHERE status='rejected'").fetchone()[0]
    kb_count = conn.execute("SELECT COUNT(*) FROM kb_items").fetchone()[0]
    chat_count = conn.execute("SELECT COUNT(*) FROM chat_logs WHERE role='user'").fetchone()[0]
    type_dist = conn.execute("SELECT type, COUNT(*) as cnt FROM applications GROUP BY type ORDER BY cnt DESC").fetchall()
    cat_dist  = conn.execute("SELECT category, COUNT(*) as cnt FROM kb_items GROUP BY category").fetchall()
    conn.close()
    return {
        "applications": {"total": total, "pending": pending, "approved": approved, "rejected": rejected},
        "kb_count": kb_count,
        "chat_count": chat_count,
        "type_distribution": [dict(r) for r in type_dist],
        "kb_categories": [dict(r) for r in cat_dist],
    }

@app.get("/api/health")
def health():
    conn = get_db()
    kb_count = conn.execute("SELECT COUNT(*) FROM kb_items").fetchone()[0]
    conn.close()
    return {
        "ok": True,
        "db": "SQLite",
        "vector_db": "ChromaDB",
        "kb_items": kb_count,
        "chroma_items": kb_collection.count(),
        "model": DS_MODEL
    }

# ─── 静态文件（前端） ─────────────────────────────────────────
app.mount("/", StaticFiles(directory=f"{BASE_DIR}/frontend", html=True), name="static")
