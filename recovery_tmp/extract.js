const fs = require('fs');
const path = require('path');

const targets = {
  '1780556143077': 'LaoNiu',
  '1780556200950': 'AuditDivergence',
  '1780556235839': 'AuditLow123',
  '1780556257055': 'AuditMA100',
  '1780556278807': 'AuditGapLimitup',
  '1780556306808': 'AuditPullbackBreakout',
  '1780556328535': 'AuditStrengthTheme',
  '1780556351771': 'TradeAnalyst',
  '1780556376664': 'TradeRisk',
  '1780556397239': 'TradePosition',
  '1780560000001': 'PaperTraderKeeper',
  '1780897961971': 'HotThemeHunter'
};

const root = path.join(__dirname, '..', 'DebugLog', 'chat');
const best = {}; // id -> { len, content, src }

function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith('.json')) scan(p);
  }
}

function scan(file) {
  let data;
  try { data = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return; }
  const arr = Array.isArray(data) ? data : [data];
  const fnMatch = path.basename(file).match(/_Agent_(\d+)_/);
  const fnId = fnMatch ? fnMatch[1] : null;
  for (const entry of arr) {
    const msgs = entry && entry.request && entry.request.messages;
    if (!Array.isArray(msgs)) continue;
    const sys = msgs.find(m => m && m.role === 'system');
    if (!sys || typeof sys.content !== 'string') continue;
    const cm = sys.content.match(/_Agent_(\d+)_/);
    const id = (cm && targets[cm[1]]) ? cm[1] : (fnId && targets[fnId] ? fnId : null);
    if (!id) continue;
    const len = sys.content.length;
    if (!best[id] || len > best[id].len) {
      best[id] = { len, content: sys.content, src: path.relative(path.join(__dirname, '..'), file) };
    }
  }
}

walk(root);

const summary = [];
for (const [id, name] of Object.entries(targets)) {
  if (best[id]) {
    const out = path.join(__dirname, name + '.rendered.txt');
    fs.writeFileSync(out, best[id].content, 'utf8');
    summary.push(`${name} (${id}): ${best[id].len} chars  <- ${best[id].src}`);
  } else {
    summary.push(`${name} (${id}): NOT FOUND in DebugLog`);
  }
}
console.log(summary.join('\n'));
