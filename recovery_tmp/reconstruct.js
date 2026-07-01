const fs = require('fs');
const path = require('path');

const names = ['LaoNiu','AuditDivergence','AuditLow123','AuditMA100','AuditGapLimitup',
  'AuditPullbackBreakout','AuditStrengthTheme','TradeAnalyst','TradeRisk','TradePosition',
  'PaperTraderKeeper','HotThemeHunter'];

// markers that indicate start of system-injected (non-agent-file) content
const markers = [
  '\n\n\n[群聊设定]',
  '\n[群聊设定]',
  '[群聊设定]',
  '为你在群聊中构建独特的个性气泡',
  '### 📂 系统组件：[VCP-Visual-Synesthesia]'
];

for (const name of names) {
  const rp = path.join(__dirname, name + '.rendered.txt');
  if (!fs.existsSync(rp)) { console.log(`${name}: rendered missing`); continue; }
  let content = fs.readFileSync(rp, 'utf8');

  let cut = content.length;
  for (const m of markers) {
    const i = content.indexOf(m);
    if (i !== -1 && i < cut) cut = i;
  }
  let body = content.slice(0, cut).replace(/\s+$/,'') + '\n';

  const bp = path.join(__dirname, name + '.body.txt');
  fs.writeFileSync(bp, body, 'utf8');

  const tail = body.slice(-140).replace(/\n/g,'\\n');
  const head = body.slice(0, 80).replace(/\n/g,'\\n');
  console.log(`=== ${name}: bodyLen=${body.length} (cut from ${content.length}) ===`);
  console.log(`   head: ${head}`);
  console.log(`   tail: ${tail}`);
}
