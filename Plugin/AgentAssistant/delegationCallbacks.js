const path = require('path');

const VALID_CALLBACK_ON = new Set(['success', 'failure', 'always']);
const DEFAULT_SUMMARY_CHARS = 800;
const DEFAULT_MAX_DEPTH = 5;
const FILE_TOOL_NAME = 'ServerFileOperator';
// delegationCallbacks.js 位于 Plugin/AgentAssistant，向上两级即项目根。
const PROJECT_ROOT = path.resolve(__dirname, '..', '..');

// 旁路注册表：delegationId -> { spec, depth }
const pendingCallbacks = new Map();

// 完成/失败标记判定。
// Agent 常在叙述/计划中顺带提到 [[TaskComplete]]/[[TaskFailed]]（例如复述委托里的产物要求），
// 因此这里取“最后一次出现”的标记为准，并让失败标记在并列或更靠后时优先生效，
// 避免把真正以 [[TaskFailed]] 收尾的任务误判为 Succeed（曾导致下游失败却照常自动交接）。
function detectDelegationCompletion(text) {
    const source = String(text === undefined || text === null ? '' : text);

    const findLastMarker = (re) => {
        let last = null;
        let match;
        while ((match = re.exec(source)) !== null) {
            last = match;
            if (match.index === re.lastIndex) re.lastIndex++;
        }
        return last;
    };

    const completionMatch = findLastMarker(/\[\[TaskComplete(?:\s*\]\]|\s[\s\S]*?\]\])/gi);
    const failureMatch = findLastMarker(/\[\[TaskFailed(?:\s*\]\]|\s[\s\S]*?\]\])/gi);
    const completionIndex = completionMatch ? completionMatch.index : -1;
    const failureIndex = failureMatch ? failureMatch.index : -1;

    const isCompleted = !!completionMatch && completionIndex > failureIndex;
    const isFailed = !!failureMatch && !isCompleted;

    if (isCompleted) {
        return { status: 'Succeed', reportStart: completionIndex + completionMatch[0].length };
    }
    if (isFailed) {
        return { status: 'Failed', reportStart: failureIndex + failureMatch[0].length };
    }
    return { status: null, reportStart: -1 };
}

function toOptionalString(value) {
    if (value === undefined || value === null) return '';
    return String(value).trim();
}

function toBool(value, defaultValue) {
    const normalized = toOptionalString(value).toLowerCase();
    if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
    if (['false', '0', 'no', 'off'].includes(normalized)) return false;
    return defaultValue;
}

function truncate(text, max) {
    const collapsed = String(text === undefined || text === null ? '' : text)
        .replace(/\s+/g, ' ')
        .trim();
    if (max && collapsed.length > max) {
        return collapsed.slice(0, max) + '...';
    }
    return collapsed;
}

function toAbsPath(relOrAbs) {
    const value = toOptionalString(relOrAbs);
    if (!value) return '';
    if (path.isAbsolute(value)) return value;
    return path.resolve(PROJECT_ROOT, value);
}

function mergeInjectTools(injectTools, autoFileTool) {
    const tools = toOptionalString(injectTools)
        .split(',')
        .map(t => t.trim())
        .filter(Boolean);

    if (autoFileTool && !tools.some(t => t.toLowerCase() === FILE_TOOL_NAME.toLowerCase())) {
        tools.push(FILE_TOOL_NAME);
    }

    return tools.join(',');
}

function buildDelegationCallbackSpec(args = {}) {
    const agentName = toOptionalString(args.callback_agent);
    const promptTemplate = toOptionalString(args.callback_prompt);

    if (!agentName && !promptTemplate) {
        return null;
    }

    if (!agentName) {
        throw new Error('callback_agent must be provided when configuring a delegation callback.');
    }

    const on = toOptionalString(args.callback_on).toLowerCase() || 'success';
    if (!VALID_CALLBACK_ON.has(on)) {
        throw new Error('callback_on must be one of: success, failure, always.');
    }

    return {
        agentName,
        promptTemplate,
        on,
        injectTools: toOptionalString(args.callback_inject_tools),
        maid: toOptionalString(args.callback_maid) || 'AgentAssistant自动交接',
        autoFileTool: toBool(args.callback_auto_file_tool, true)
    };
}

function shouldRunDelegationCallback(callbackSpec, completionStatus) {
    if (!callbackSpec) return false;

    const normalizedStatus = String(completionStatus || '').toLowerCase();
    if (callbackSpec.on === 'always') return true;
    if (callbackSpec.on === 'success') return normalizedStatus === 'succeed';
    if (callbackSpec.on === 'failure') return normalizedStatus === 'failed';
    return false;
}

function buildDefaultHandoffPrompt(data = {}) {
    return [
        '[自动交接信封] 上游 Agent「{{source_agent}}」的委托任务已结束。',
        '- 委托ID: {{delegation_id}}',
        '- 状态: {{status}}',
        '',
        '成果摘要:',
        '{{report_summary}}',
        '',
        '完整报告文件（绝对路径）: {{archive_abspath}}',
        '请先使用 ServerFileOperator 的 ReadFile 命令读取上述文件获取完整内容，再开展你的工作。'
    ].join('\n');
}

function renderDelegationCallbackPrompt(callbackSpec, data = {}) {
    const template = (callbackSpec && callbackSpec.promptTemplate)
        ? callbackSpec.promptTemplate
        : buildDefaultHandoffPrompt(data);

    const values = {
        delegation_id: data.delegationId,
        source_agent: data.sourceAgent,
        status: data.status,
        report: data.report,
        report_summary: data.reportSummary,
        archive_path: data.archivePath,
        archive_abspath: data.archiveAbsPath
    };

    return Object.entries(values).reduce((prompt, [key, value]) => {
        return prompt.replaceAll(`{{${key}}}`, value === undefined || value === null ? '' : String(value));
    }, template);
}

function registerDelegationCallback(delegationId, args = {}, { agents } = {}) {
    const spec = buildDelegationCallbackSpec(args);
    if (spec === null) {
        return null;
    }

    if (agents && !agents[spec.agentName]) {
        const availableAgentNames = Object.keys(agents);
        let errorMessage = `回调目标 Agent '${spec.agentName}' 未找到。`;
        errorMessage += availableAgentNames.length > 0
            ? ` 当前可用的 Agent 有: ${availableAgentNames.join(', ')}。`
            : ` 当前没有加载任何 Agent。`;
        throw new Error(errorMessage);
    }

    const depth = parseInt(args.__handoff_depth, 10) || 0;
    pendingCallbacks.set(delegationId, { spec, depth });
    return spec;
}

async function runDelegationHandoffIfNeeded(delegationId, ctx = {}) {
    const entry = pendingCallbacks.get(delegationId);
    if (!entry) return;
    pendingCallbacks.delete(delegationId);

    const { spec, depth } = entry;
    const { status, report, archivePath, agentBaseName, processToolCall, pushVcpInfo, debugMode } = ctx;

    if (!shouldRunDelegationCallback(spec, status)) return;

    if (depth >= DEFAULT_MAX_DEPTH) {
        if (typeof pushVcpInfo === 'function') {
            pushVcpInfo({
                type: 'warning',
                source: 'AgentAssistant',
                message: `异步委托任务 [${delegationId}] 已达交接链深度上限 (${DEFAULT_MAX_DEPTH})，已停止继续自动交接以防止死循环。`
            });
        }
        return;
    }

    const archiveAbsPath = toAbsPath(archivePath);
    const data = {
        delegationId,
        sourceAgent: agentBaseName,
        status,
        report: report || '',
        reportSummary: truncate(report, DEFAULT_SUMMARY_CHARS),
        archivePath: archivePath || '',
        archiveAbsPath
    };

    await triggerDelegationHandoff(spec, data, { processToolCall, pushVcpInfo, debugMode, depth });
}

async function triggerDelegationHandoff(spec, data, { processToolCall, pushVcpInfo, debugMode, depth = 0 } = {}) {
    try {
        const callbackPrompt = renderDelegationCallbackPrompt(spec, data);
        const injectTools = mergeInjectTools(spec.injectTools, spec.autoFileTool);
        const result = await processToolCall({
            agent_name: spec.agentName,
            prompt: callbackPrompt,
            maid: spec.maid,
            task_delegation: 'true',
            inject_tools: injectTools,
            __handoff_depth: depth + 1
        });

        if (typeof pushVcpInfo === 'function') {
            pushVcpInfo({
                type: 'info',
                source: 'AgentAssistant',
                message: `异步委托任务 [${data.delegationId}] 已自动交接给 ${spec.agentName}。`
            });
        }

        if (debugMode) {
            console.error(`[AgentAssistant Delegation] Handoff for ${data.delegationId} submitted to ${spec.agentName}:`, result?.content?.[0]?.text || result);
        }
    } catch (error) {
        console.error(`[AgentAssistant Delegation] Failed to trigger handoff for ${data.delegationId} to ${spec.agentName}:`, error.message);
        if (typeof pushVcpInfo === 'function') {
            pushVcpInfo({
                type: 'error',
                source: 'AgentAssistant',
                message: `异步委托任务 [${data.delegationId}] 自动交接给 ${spec.agentName} 失败: ${error.message}`
            });
        }
    }
}

module.exports = {
    detectDelegationCompletion,
    buildDelegationCallbackSpec,
    renderDelegationCallbackPrompt,
    shouldRunDelegationCallback,
    buildDefaultHandoffPrompt,
    registerDelegationCallback,
    runDelegationHandoffIfNeeded,
    triggerDelegationHandoff,
    mergeInjectTools
};
