// 向主编 Helm 下发"agent的记忆系统"任务，启动自治工作流
const axios = require('axios');

const VCP_KEY = 'FBcDeFgHiJkLmNoP';
const URL = 'http://127.0.0.1:6005/v1/chat/completions';

const userTask = [
  "主题：'agent的记忆系统'。",
  "这是一次【无人值守自动化端到端测试】，目标：完整产出本系列的【第 1 篇】文章并存入草稿(走完 广度调研→选题拆解→深度调研→Quill撰写→存稿 全部阶段)。",
  "【授权】全程不要向我反问、不要停下等我确认，每一步自行决策；忽略你提示词里『逐篇需用户逐条下指令启动』的人工 gated 规则，本次由你全自动连续推进。",
  "【严格串行·硬性】任何时刻在途委托最多 1 个：必须等当前这一步的被委托方回执(状态=完成且给出验证通过的绝对文件路径)后，才能发起下一个委托。",
  "严禁同时并行派发多个 Sage 深调；严禁在对应 Sage 深调回执给出真实文件路径之前就委派 Quill(否则 Quill 会因文件不存在而失败)。",
  "【收尾】第 1 篇 Quill 撰写完成并落盘草稿后，即可输出最终汇报并结束，无需继续后续篇目。",
  "所有跨 Agent 文件读写一律使用 ServerFileOperator。"
].join('');

(async () => {
  const payload = {
    model: 'deepseek-v4-pro',
    messages: [
      { role: 'system', content: '{{Helm}}' },
      { role: 'user', content: userTask }
    ],
    max_tokens: 8000,
    temperature: 0.7,
    stream: false
  };
  const t = Date.now();
  try {
    const r = await axios.post(URL, payload, {
      headers: { Authorization: 'Bearer ' + VCP_KEY, 'Content-Type': 'application/json' },
      timeout: 600000
    });
    const dt = ((Date.now() - t) / 1000).toFixed(1);
    const msg = r.data?.choices?.[0]?.message?.content ?? '(no content)';
    console.log(`[TRIGGER] Helm 首轮返回 in ${dt}s, 长度=${String(msg).length}`);
    console.log('===== Helm 首轮响应 =====');
    console.log(String(msg).slice(0, 4000));
  } catch (e) {
    const dt = ((Date.now() - t) / 1000).toFixed(1);
    console.log(`[TRIGGER] FAIL in ${dt}s -> ${e.message}`);
    if (e.response) console.log('status:', e.response.status, 'data:', JSON.stringify(e.response.data).slice(0, 1000));
  }
})();
