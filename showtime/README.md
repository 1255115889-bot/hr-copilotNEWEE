# AI员工政策顾问 Demo (Showtime)

基于《公司综合管理制度汇编》的智能HR政策问答系统

## 技术栈
- **前端**：原生 HTML + CSS，企业HR风格
- **后端**：FastAPI (Python) + uvicorn
- **数据库**：SQLite（对话记录持久化）
- **向量检索**：ChromaDB（RAG语义检索）
- **AI**：DeepSeek deepseek-v4-pro

## 知识库覆盖
- 考勤管理制度（15条）：打卡/迟到/加班/请假/出差
- 育儿假专项政策（5条）：天数/工资/申请流程
- 薪酬核算细化规则（9条）：日薪/扣款/年终奖/离职结算

## 访问地址
https://cccarolyn.top/showtime

## 本地运行
pip install fastapi uvicorn chromadb httpx
uvicorn api.main:app --host 0.0.0.0 --port 3005
