# WeWriteCore

**这是一个共享底座目录, 不是一个可被 AI 调用的 VCP 插件**
(因此本目录下没有 `plugin-manifest.json`, 加载器会自动跳过)。

它的作用是把 [oaker-io/wewrite](https://github.com/oaker-io/wewrite)
项目的 **原样源码** 嵌入到 VCP 内, 并对外暴露一组 Python 工具函数,
供 `WeWriteRender` / `WeWritePublish` / `WeWriteThemes` 等业务插件调用。

## 目录结构

```
Plugin/WeWriteCore/
├── README.md              # 本说明
├── bootstrap.py           # 共享底座: env → wewrite config 桥接 + 子进程辅助
├── config.env.example     # 所有 WeWrite* 插件共用的环境变量模板
└── wewrite/               # ★ 上游 wewrite 源码, 完整保留, 不可改动
    ├── SKILL.md           # 原 8 步管线提示词 (Agent 提示词来源)
    ├── toolkit/           # converter / theme / publisher / wechat_api / image_gen
    ├── scripts/           # fetch_hotspots / seo_keywords / humanness_score / ...
    ├── personas/          # 5 套写作人格 (yaml)
    ├── references/        # 12 份按需加载的策略文档
    └── ...
```

## 设计原则

1. **零修改源码**: `wewrite/` 子目录是 wewrite 项目的完整克隆 (排除运行
   时机密文件: `config.yaml` / `style.yaml` / `history.yaml` / `output/`
   / `lessons/` 等)。后续从上游更新只需:
   ```bash
   robocopy /MIR  <wewrite repo>  Plugin/WeWriteCore/wewrite \
     /XD .git .github dist output corpus lessons references\exemplars \
     /XF style.yaml config.yaml writing-config.yaml history.yaml playbook.md
   ```
2. **配置桥接由 bootstrap 完成**: VCP 通过 `config.env` 暴露环境变量,
   `bootstrap.ensure_runtime()` 在每次插件 spawn 时根据 env 渲染出
   `wewrite/config.yaml` 和 `wewrite/style.yaml`。wewrite 内部代码完全
   感知不到 VCP 的存在。
3. **stdio JSON 协议封装**: 提供 `read_args`、`success`、`failure`、
   `pick` 等帮助函数, 让业务插件的入口可以保持 30 行以内。
4. **超栈追踪 (Hyper-Stack-Trace) 支持**: `materialize_image()` 在遇到
   远程节点的 `file://` URL 且本地不存在时, 会抛出带
   `code=FILE_NOT_FOUND_LOCALLY` + `failedParameter` 的结构化异常;
   插件入口用 `emit_file_not_found()` 直接转发给 VCP 主服务, 实现
   跨节点拉取。

## 依赖

`wewrite/requirements.txt` 列出的 Python 包:

```
markdown >=3.5         # Markdown 解析
beautifulsoup4 >=4.12  # HTML 处理
cssutils >=2.9         # 主题 CSS 解析
requests >=2.31        # 微信 API + 图片下载
camoufox[geoip] >=0.4  # 公众号反爬抓取 (fetch_article.py 才用)
pyyaml >=6.0           # 配置文件
Pygments >=2.15        # 代码块高亮
Pillow >=10.0          # 图片压缩
```

各 WeWrite* 插件目录内会通过 `requirements.txt` 复用该列表。

## 配置变量速查

完整列表见 `config.env.example`。最常用的:

| 变量 | 含义 | 用于 |
|------|------|------|
| `WECHAT_APPID` / `WECHAT_SECRET` | 公众号 API 凭据 | WeWritePublish |
| `WECHAT_AUTHOR` / `WEWRITE_AUTHOR` | 默认署名 | WeWritePublish |
| `WEWRITE_DEFAULT_THEME` | 默认排版主题 | WeWriteRender / WeWritePublish |
| `WEWRITE_NAME` / `WEWRITE_INDUSTRY` / `WEWRITE_TOPICS` | 公众号画像 | 写作类插件 (后续接入) |
| `WEWRITE_WRITING_PERSONA` | 写作人格 | 写作类插件 (后续接入) |

## 如何在新插件中使用

```python
# Plugin/WeWriteSomething/runner.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "WeWriteCore"))

import bootstrap

def main() -> None:
    try:
        args = bootstrap.read_args()
        paths = bootstrap.ensure_runtime()           # 确保 wewrite 配置就绪
        bootstrap.inject_toolkit_path()              # 允许直接 import converter / theme
        # ... 业务逻辑 ...
        bootstrap.success({"some": "result"})
    except FileNotFoundError as e:
        if getattr(e, "code", None) == "FILE_NOT_FOUND_LOCALLY":
            bootstrap.emit_file_not_found(e)        # 触发 VCP 超栈追踪
        bootstrap.failure(str(e))
    except Exception as e:
        bootstrap.failure(str(e))

if __name__ == "__main__":
    main()
```

## 已知例外

- `wewrite/references/exemplars/` 是用户范文库, 不打包进 VCP, 由
  `extract_exemplar.py` 在运行时按需创建。
- `wewrite/.github/` 是上游的 CI 配置, 不在 VCP 内运行。
- `wewrite/evals/` 是上游的质量评估数据, 仅供参考。
