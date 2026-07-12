# redhawk

基于 [deepagents](https://github.com/langchain-ai/deepagents) 构建的轻量安全红队智能体。
这是基础智能体循环——可扩展至完整的攻击面覆盖（Web/二进制漏洞挖掘、漏洞利用、多阶段渗透、云安全、防御规避）。

## 快速开始

```bash
uv run python main.py        # Textual TUI（默认）
uv run python main.py --cli  # 命令行 REPL 回退
```

在 `.env` 中配置模型（OpenAI 兼容接口）：

```
OPENAI_API_KEY=...
OPENAI_API_BASE_URL=https://api.deepseek.com
OPENAI_API_MODEL=deepseek-v4-flash
```

然后输入目标，例如 `扫描 10.10.14.5 并报告开放服务`。

## 架构

```
main.py              # 便捷启动器（委托到包入口）
src/
└── tinyagent/
    ├── __init__.py      # 导出 build_agent()
    ├── __main__.py      # 包入口（python -m tinyagent）
    ├── config.py        # 加载 .env，构建 LLM 客户端（ChatDeepSeek）
    ├── prompts.py       # 系统提示词（协调器 + 7 个子代理）
    ├── builder.py       # 组装智能体 + 子代理  <-- 扩展点
    ├── textual_app.py   # Textual TUI（聊天面板、侧边栏、中间件）
    ├── tui_cli.py       # CLI 回退 REPL
    ├── tracer.py        # CLI 工具调用追踪中间件
    ├── workspace.py     # `.redhawk/` 工作区设置 + CompositeBackend
    ├── memory.py        # 跨会话持久记忆
    └── mcp_tools.py     # Tavily + Exa 网络搜索工具加载器
```

协调器智能体通过 deepagents 内置的 `task` 工具规划任务并委派给专家子代理。
每个专家运行在独立的上下文中。后端提供内置工具集：文件工具（ls/read_file/
write_file/edit_file/glob/grep）、shell `execute` 工具以及 `task`。

## 工作区（`.redhawk/`）

启动时，智能体会在当前工作目录下创建 `.redhawk/` 目录作为工作区。**所有工具均以该目录为工作目录**：

- 文件工具（ls/read_file/write_file/edit_file/glob/grep）和 shell `execute` 工具均以 `.redhawk/` 为当前目录，因此相对路径会落在其中。
  路径按原样使用（`virtual_mode=False`）——这使得文件工具和 shell 工具基于相同的真实路径，避免路径嵌套。
- 工作区的绝对路径会被注入到协调器提示词中，确保 LLM 写入正确位置。
- 注意：这仅是工作目录约定，并非硬沙箱——shell `execute` 工具在设计上不受限制（渗透测试智能体必须能够访问外部目标）。

## 记忆（跨会话）

智能体拥有持久化的长期记忆，存储在 `.redhawk/memories/AGENTS.md`，
通过后端以虚拟 `/memories/` 文件系统的形式暴露。它在启动时被加载到系统提示词中，
因此智能体能够在**不同运行之间**保持先验知识（渗透发现、payload 笔记、操作员偏好）。
智能体通过 `edit_file` 自行更新记忆，你也可以随时手动编辑该 markdown 文件。

实现方式：`CompositeBackend` 将 `/memories/` 路由到
`FilesystemBackend`（真实磁盘 markdown，零额外依赖）——相比 `StoreBackend`，
对于单用户 CLI 工具，持久性和人类可读性比多用户命名空间更重要。

## 添加新的安全领域

只需两步：

1. 在 `src/tinyagent/prompts.py` 中添加提示词：

   ```python
   BINARY_EXPLOIT_PROMPT = "你是一名二进制漏洞利用专家..."
   ```

2. 在 `src/tinyagent/builder.py` 中追加一条字典：

   ```python
   SUBAGENTS = [
       ...,
       {
           "name": "binary-exploit",
           "description": "逆向二进制文件并发现内存损坏漏洞。",
           "system_prompt": BINARY_EXPLOIT_PROMPT,
       },
   ]
   ```

完成——协调器会自动将任务委派给它。

## 范围与安全

仅限用于已获得明确授权的测试目标。协调器提示词在执行前强制执行授权检查。
