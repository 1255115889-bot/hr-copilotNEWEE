import express from 'express';
import { createServer } from 'http';
import { readFileSync, existsSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3001;

app.use(express.json({ limit: '10mb' }));
app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, x-api-key, anthropic-version, anthropic-dangerous-direct-browser-access');
  if (req.method === 'OPTIONS') { res.sendStatus(200); return; }
  next();
});

const frontendPath = join(__dirname, '../frontend');
app.use(express.static(frontendPath));

const DB_PATH = join(__dirname, 'data.json');
function loadDB() {
  if (!existsSync(DB_PATH)) return { kb: [], applications: [] };
  try { return JSON.parse(readFileSync(DB_PATH, 'utf-8')); } catch { return { kb: [], applications: [] }; }
}
function saveDB(data) { writeFileSync(DB_PATH, JsON.stringify(data, null, 2)); }

app.get('/api/kb', (req, res) => res.json(loadDB().kb));
app.post('/api/kb', (req, res) => { const db=loadDB(); const item={...req.body,id:Date.now(),updatedAt:new Date().toISOString().slice(0,10)}; db.kb.unshift(item); saveDB(db); res.json(item); });
app.put('/api/kb/:id', (req, res) => { const db=loadDB(); db.kb=db.kb.map(k=>k.id===parseInt(req.params.id)?{...k,...req.body,updatedAt:new Date().toISOString().slice(0,10)}:k); saveDB(db); res.json({ok:true}); });
app.delete('/api/kb/:id', (req, res) => { const db=loadDB(); db.kb=db.kb.filter(k=>k.id!==parseInt(req.params.id)); saveDB(db); res.json({ok:true}); });
app.get('/api/applications', (req, res) => res.json(loadDB().applications));
app.post('/api/applications', (req, res) => { const db=loadDB(); const a={...req.body,id:'APP'+String(Date.now()).slice(-6),status:'pending',submitAt:new Date().toLocaleString('zh-CN')}; db.applications.unshift(a); saveDB(db); res.json(a); });
app.put('/api/applications/:id/approve', (req, res) => { const db=loadDB(); db.applications=db.applications.map(a=>a.id===req.params.id?{...a,status:'approved',approvedAt:new Date().toLocaleString('zh-CN')}:a); saveDB(db); res.json({ok:true}); });
app.put('/api/applications/:id/reject', (req, res) => { const db=loadDB(); db.applications=db.applications.map(a=>a.id===req.params.id?{...a,status:'rejected',rejectReason:req.body.reason||'Not approved'}:a); saveDB(db); res.json({ok:true}); });
app.get('/api/health', (req, res) => res.json({ ok: true }));
app.get('*', (req, res) => res.sendFile(join(frontendPath, 'index.html')));
createServer(app).listen(PORT, () => console.log('HR Copilot running on port ' + PORT));
