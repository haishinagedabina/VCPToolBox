const assert = require('assert');
const path = require('path');

const {
    detectDelegationCompletion,
    buildDelegationCallbackSpec,
    renderDelegationCallbackPrompt,
    shouldRunDelegationCallback,
    buildDefaultHandoffPrompt,
    registerDelegationCallback,
    runDelegationHandoffIfNeeded,
    mergeInjectTools
} = require('./delegationCallbacks');

// ── detectDelegationCompletion：完成/失败标记判定 ──
// 仅有完成标记 → Succeed，且报告从标记之后开始
{
    const r = detectDelegationCompletion('做完了。\n[[TaskComplete]]\n这是完成报告');
    assert.strictEqual(r.status, 'Succeed');
    assert.strictEqual('做完了。\n[[TaskComplete]]\n这是完成报告'.substring(r.reportStart).trim(), '这是完成报告');
}
// 仅有失败标记 → Failed
{
    const r = detectDelegationCompletion('搞不定。\n[[TaskFailed]]\n失败原因');
    assert.strictEqual(r.status, 'Failed');
    assert.strictEqual('搞不定。\n[[TaskFailed]]\n失败原因'.substring(r.reportStart).trim(), '失败原因');
}
// 无任何标记 → null（继续心跳）
assert.strictEqual(detectDelegationCompletion('还在跑，[[NextHeartbeat::60]]').status, null);
assert.strictEqual(detectDelegationCompletion('').status, null);
// 回归本次 bug：早期叙述里顺带提到 [[TaskComplete]]，结尾才是真正的 [[TaskFailed]] → 必须判 Failed
{
    const text = '产物要求：[[TaskComplete]] 回执必须包含验证路径。\n……执行后落盘验证失败……\n[[TaskFailed]]\n失败原因：ENOENT';
    const r = detectDelegationCompletion(text);
    assert.strictEqual(r.status, 'Failed');
    assert.strictEqual(text.substring(r.reportStart).trim(), '失败原因：ENOENT');
}
// 对称场景：先失败标记、后完成标记（末位为准）→ Succeed
{
    const text = '一开始我以为 [[TaskFailed]]，但重试后成功了。\n[[TaskComplete]]\n最终报告';
    const r = detectDelegationCompletion(text);
    assert.strictEqual(r.status, 'Succeed');
    assert.strictEqual(text.substring(r.reportStart).trim(), '最终报告');
}

const spec = buildDelegationCallbackSpec({
    callback_agent: '主编',
    callback_prompt: [
        'event={{status}}',
        'agent={{source_agent}}',
        'id={{delegation_id}}',
        'path={{archive_path}}',
        'report={{report}}'
    ].join('\n'),
    callback_on: 'success',
    callback_inject_tools: 'AgentAssistant,ServerFileOperator',
    callback_maid: '自动交接'
});

assert.deepStrictEqual(spec, {
    agentName: '主编',
    promptTemplate: [
        'event={{status}}',
        'agent={{source_agent}}',
        'id={{delegation_id}}',
        'path={{archive_path}}',
        'report={{report}}'
    ].join('\n'),
    on: 'success',
    injectTools: 'AgentAssistant,ServerFileOperator',
    maid: '自动交接',
    autoFileTool: true
});

assert.strictEqual(shouldRunDelegationCallback(spec, 'Succeed'), true);
assert.strictEqual(shouldRunDelegationCallback(spec, 'Failed'), false);
assert.strictEqual(shouldRunDelegationCallback({ ...spec, on: 'always' }, 'Failed'), true);
assert.strictEqual(shouldRunDelegationCallback(null, 'Succeed'), false);

const prompt = renderDelegationCallbackPrompt(spec, {
    delegationId: 'aa-delegation-1',
    sourceAgent: 'Sage',
    status: 'Succeed',
    report: '完成报告正文',
    archivePath: 'E:\\VCPChat\\VCPToolBox\\file\\document\\AgentTask\\Sage_aa-delegation-1.md'
});

assert.ok(prompt.includes('event=Succeed'));
assert.ok(prompt.includes('agent=Sage'));
assert.ok(prompt.includes('id=aa-delegation-1'));
assert.ok(prompt.includes('report=完成报告正文'));
assert.ok(prompt.includes('path=E:\\VCPChat\\VCPToolBox\\file\\document\\AgentTask\\Sage_aa-delegation-1.md'));

assert.strictEqual(buildDelegationCallbackSpec({
    callback_agent: '主编',
    callback_prompt: 'done'
}).on, 'success');

assert.throws(() => buildDelegationCallbackSpec({
    callback_agent: '主编',
    callback_prompt: 'done',
    callback_on: 'sometimes'
}), /callback_on/);

// 新增：只给 callback_agent（不给 prompt）→ 使用默认信封
const specNoPrompt = buildDelegationCallbackSpec({ callback_agent: '主编' });
assert.strictEqual(specNoPrompt.promptTemplate, '');
assert.strictEqual(specNoPrompt.autoFileTool, true);

// 新增：给了 prompt 却没 agent → 抛错
assert.throws(() => buildDelegationCallbackSpec({ callback_prompt: 'done' }), /callback_agent/);

// 新增：两者都为空 → null
assert.strictEqual(buildDelegationCallbackSpec({}), null);

// 新增：默认信封渲染（占位符已替换，含 ServerFileOperator 读取指引）
const defaultRendered = renderDelegationCallbackPrompt(specNoPrompt, {
    delegationId: 'aa-delegation-2',
    sourceAgent: 'Sage',
    status: 'Succeed',
    report: '一段需要被折叠的   报告  正文',
    reportSummary: '一段需要被折叠的 报告 正文',
    archivePath: 'file/document/AgentTask/Sage_aa-delegation-2.md',
    archiveAbsPath: 'E:\\VCPChat\\VCPToolBox\\file\\document\\AgentTask\\Sage_aa-delegation-2.md'
});
assert.ok(defaultRendered.includes('ServerFileOperator'));
assert.ok(defaultRendered.includes('Sage'));
assert.ok(defaultRendered.includes('一段需要被折叠的 报告 正文'));
assert.ok(defaultRendered.includes('E:\\VCPChat\\VCPToolBox\\file\\document\\AgentTask\\Sage_aa-delegation-2.md'));
assert.ok(!defaultRendered.includes('{{report_summary}}'));
assert.ok(!defaultRendered.includes('{{archive_abspath}}'));

// 新增：buildDefaultHandoffPrompt 含占位符
const tpl = buildDefaultHandoffPrompt({});
assert.ok(tpl.includes('{{report_summary}}'));
assert.ok(tpl.includes('{{archive_abspath}}'));
assert.ok(tpl.includes('ServerFileOperator'));

// 新增：{{report_summary}} 与 {{archive_abspath}} 替换正确（自定义模板）
const customSpec = buildDelegationCallbackSpec({
    callback_agent: '主编',
    callback_prompt: 'summary={{report_summary}};abs={{archive_abspath}}'
});
const customRendered = renderDelegationCallbackPrompt(customSpec, {
    reportSummary: 'SUMMARY_X',
    archiveAbsPath: 'C:\\abs\\path.md'
});
assert.strictEqual(customRendered, 'summary=SUMMARY_X;abs=C:\\abs\\path.md');

// 新增：mergeInjectTools 行为
assert.strictEqual(mergeInjectTools('AgentAssistant', true), 'AgentAssistant,ServerFileOperator');
assert.strictEqual(mergeInjectTools('AgentAssistant,ServerFileOperator', true), 'AgentAssistant,ServerFileOperator');
assert.strictEqual(mergeInjectTools('AgentAssistant,serverfileoperator', true), 'AgentAssistant,serverfileoperator');
assert.strictEqual(mergeInjectTools('AgentAssistant', false), 'AgentAssistant');
assert.strictEqual(mergeInjectTools('', true), 'ServerFileOperator');
assert.strictEqual(mergeInjectTools(' a , , b ', false), 'a,b');

// 新增：register + run 闭环
(async () => {
    // 闭环 1：成功条件触发，自动注入 ServerFileOperator，深度为 1
    {
        const calls = [];
        const mockProcessToolCall = async (callArgs) => { calls.push(callArgs); return { content: [{ text: 'ok' }] }; };
        const id = 'aa-delegation-loop-1';
        const registered = registerDelegationCallback(id, {
            callback_agent: '主编',
            callback_inject_tools: 'TopicSponsor'
        }, { agents: { '主编': {} } });
        assert.ok(registered);
        await runDelegationHandoffIfNeeded(id, {
            status: 'Succeed',
            report: '报告',
            archivePath: 'file/document/AgentTask/x.md',
            agentBaseName: 'Sage',
            processToolCall: mockProcessToolCall
        });
        assert.strictEqual(calls.length, 1);
        assert.strictEqual(calls[0].task_delegation, 'true');
        assert.strictEqual(calls[0].__handoff_depth, 1);
        assert.ok(calls[0].inject_tools.split(',').includes('ServerFileOperator'));
        assert.ok(calls[0].inject_tools.split(',').includes('TopicSponsor'));
    }

    // 闭环 2：status 不满足 on 条件 → 不触发
    {
        const calls = [];
        const mockProcessToolCall = async (callArgs) => { calls.push(callArgs); };
        const id = 'aa-delegation-loop-2';
        registerDelegationCallback(id, { callback_agent: '主编', callback_on: 'success' }, { agents: { '主编': {} } });
        await runDelegationHandoffIfNeeded(id, {
            status: 'Failed',
            report: '报告',
            agentBaseName: 'Sage',
            processToolCall: mockProcessToolCall
        });
        assert.strictEqual(calls.length, 0);
    }

    // 闭环 3：防环——注册时 __handoff_depth=5 (>=DEFAULT_MAX_DEPTH) → 不触发
    {
        const calls = [];
        const warnings = [];
        const mockProcessToolCall = async (callArgs) => { calls.push(callArgs); };
        const id = 'aa-delegation-loop-3';
        registerDelegationCallback(id, {
            callback_agent: '主编',
            callback_on: 'always',
            __handoff_depth: 5
        }, { agents: { '主编': {} } });
        await runDelegationHandoffIfNeeded(id, {
            status: 'Succeed',
            report: '报告',
            agentBaseName: 'Sage',
            processToolCall: mockProcessToolCall,
            pushVcpInfo: (info) => warnings.push(info)
        });
        assert.strictEqual(calls.length, 0);
        assert.strictEqual(warnings.length, 1);
        assert.strictEqual(warnings[0].type, 'warning');
    }

    // 闭环 4：未注册的 id → run 安全 no-op
    {
        const calls = [];
        await runDelegationHandoffIfNeeded('nonexistent-id', {
            status: 'Succeed',
            processToolCall: async (a) => { calls.push(a); }
        });
        assert.strictEqual(calls.length, 0);
    }

    // 新增：回调目标 Agent 不存在 → register 抛错
    assert.throws(() => registerDelegationCallback('aa-delegation-loop-5', {
        callback_agent: '不存在的Agent'
    }, { agents: { '主编': {} } }), /未找到/);

    console.log('delegationCallbacks tests passed');
})().catch(err => {
    console.error(err);
    process.exit(1);
});
